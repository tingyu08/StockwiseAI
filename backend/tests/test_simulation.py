import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.models import AiReport, DailyPrice, SimOrder, Stock, WatchlistItem
from app.services.sim.decision import run_decisions
from app.services.sim import engine as sim_engine
from app.services.sim.engine import calc_fee, fill_pending_orders, get_or_create_account
from app.services.sim.portfolio import current_positions, equity_curve


def _seed_stock(db, symbol, market="TW", closes=None, ai_managed=True):
    stock = Stock(symbol=symbol, market=market, name=f"測試{symbol}", currency="TWD", kind="stock")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    closes = closes or [100.0] * 40
    d = date.today() - timedelta(days=len(closes) + 60)
    added = 0
    while added < len(closes):
        if d.weekday() < 5:
            c = closes[added]
            db.add(DailyPrice(stock_id=stock.id, date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000))
            added += 1
        d += timedelta(days=1)
    db.add(WatchlistItem(stock_id=stock.id, ai_managed=ai_managed))
    db.commit()
    return stock


def _add_report(db, stock, action="buy", confidence=0.8, stop_loss=80.0):
    trade_date = db.execute(
        __import__("sqlalchemy").select(DailyPrice.date)
        .where(DailyPrice.stock_id == stock.id)
        .order_by(DailyPrice.date.desc()).limit(1)
    ).scalar_one()
    payload = {
        "symbol": stock.symbol, "action": action, "confidence": confidence,
        "target_price_low": 90, "target_price_high": 120, "stop_loss": stop_loss,
        "reasoning": "測試", "risks": [],
        "scenarios": {k: {"target_price": 100, "trigger_condition": "t", "probability": p}
                      for k, p in (("bull", 0.3), ("base", 0.5), ("bear", 0.2))},
    }
    report = AiReport(
        stock_id=stock.id, trade_date=trade_date, provider="test", model="test",
        prompt_version="v1", kind="routine", action=action, confidence=confidence,
        payload_json=json.dumps(payload),
    )
    db.add(report)
    db.commit()
    return report


# ---- 手續費 ----

def test_tw_buy_fee_min_20():
    assert calc_fee("TW", "buy", 1000) == 20.0  # 0.1425% = 1.4 → 最低 20

def test_tw_sell_fee_includes_tax():
    fee = calc_fee("TW", "sell", 1_000_000)
    assert fee == pytest.approx(1425 + 3000)

def test_us_fee_zero():
    assert calc_fee("US", "buy", 50_000) == 0.0


# ---- 決策 → 撮合 → 持倉 ----

def test_buy_decision_and_fill_flow(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "8001")
        _add_report(db, stock, action="buy", confidence=0.8)

        result = run_decisions(db, "TW")
        assert result["orders_created"] == 1

        # 委託尚未成交（created_at 為今日，需下一交易日價格）——
        # 測試資料價格截至過去日期，將 created_at 往回調使其可成交
        order = db.execute(
            __import__("sqlalchemy").select(SimOrder).where(
                SimOrder.stock_id == stock.id
            )
        ).scalars().first()
        # 種子價格區間約為 [today-100, today-44]，回調至 90 天前確保其後仍有交易日
        order.created_at = datetime.now() - timedelta(days=90)
        db.commit()

        fill = fill_pending_orders(db, "TW")
        assert fill["filled"] == 1

        account = get_or_create_account(db, "TW")
        positions = current_positions(db, account)
        assert positions.get(stock.id, 0) > 0
        # 部位上限 20%：成交金額不超過權益兩成（含些許費用緩衝）
        db.refresh(order)
        assert float(order.qty) * float(order.fill_price) <= 1_000_000 * 0.20 * 1.01
        # 現金守恆
        assert float(account.cash) == pytest.approx(
            1_000_000 - float(order.qty) * float(order.fill_price) - float(order.fee), abs=0.01
        )
    finally:
        db.close()


def test_low_confidence_buy_not_executed(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "8002")
        _add_report(db, stock, action="buy", confidence=0.5)  # < 0.7
        result = run_decisions(db, "TW")
        symbols = [o["symbol"] for o in result["orders"]]
        assert "8002" not in symbols
    finally:
        db.close()


def test_sell_without_position_not_executed(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "8003")
        _add_report(db, stock, action="sell", confidence=0.9)
        result = run_decisions(db, "TW")
        symbols = [o["symbol"] for o in result["orders"]]
        assert "8003" not in symbols
    finally:
        db.close()


def test_equity_curve_replay(client):
    # 用 US 帳戶隔離，避免前面測試在 TW 帳戶留下的訂單影響重放結果
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "US")
        stock = _seed_stock(db, "EQTY", market="US", closes=[100.0] * 10 + [110.0] * 10)
        prices = db.execute(
            __import__("sqlalchemy").select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id).order_by(DailyPrice.date)
        ).scalars().all()
        buy_day = prices[5].date
        db.add(SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=1000,
            fill_price=100.0, fee=142.5, status="filled", decided_by="ai",
            filled_at=datetime.combine(buy_day, datetime.min.time()),
        ))
        db.commit()

        curve = equity_curve(db, account)
        assert len(curve) > 0
        initial = float(account.initial_cash)
        # 買進日：現金 - 成本 - 手續費 + 市值（100×1000）≈ 初始 - 142.5
        assert curve[0]["equity"] == pytest.approx(initial - 142.5, abs=0.01)
        # 漲到 110 後：+1000 × 10 = +10000
        assert curve[-1]["equity"] == pytest.approx(initial - 142.5 + 10_000, abs=0.01)
    finally:
        db.close()


def test_only_one_pending_order_per_account_and_stock(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "8010")
        account = get_or_create_account(db, "TW")
        db.add_all([
            SimOrder(
                account_id=account.id, stock_id=stock.id, side="buy", qty=1,
                status="pending", decided_by="ai",
            ),
            SimOrder(
                account_id=account.id, stock_id=stock.id, side="buy", qty=1,
                status="pending", decided_by="ai",
            ),
        ])

        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_sell_order_cannot_fill_more_than_current_position(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "8011")
        account = get_or_create_account(db, "TW")
        cash_before = float(account.cash)
        db.add(SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=10,
            fill_price=100, fee=20, status="filled", decided_by="ai",
            filled_at=datetime.now() - timedelta(days=130),
        ))
        sell = SimOrder(
            account_id=account.id, stock_id=stock.id, side="sell", qty=15,
            status="pending", decided_by="ai",
            created_at=datetime.now() - timedelta(days=120),
        )
        db.add(sell)
        db.commit()

        result = fill_pending_orders(db, "TW")

        db.refresh(sell)
        db.refresh(account)
        assert result["rejected"] == 1
        assert sell.status == "rejected"
        assert sell.reject_reason == "賣出數量超過目前持倉"
        assert float(account.cash) == cash_before
        assert current_positions(db, account)[stock.id] == 10
    finally:
        db.close()


def test_pending_order_can_be_claimed_only_once(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "8012")
        account = get_or_create_account(db, "TW")
        order = SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=1,
            status="pending", decided_by="ai",
        )
        db.add(order)
        db.commit()
        claim = getattr(sim_engine, "_claim_pending_order", lambda *_: False)

        assert claim(db, order.id) is True
        assert claim(db, order.id) is False
    finally:
        db.rollback()
        db.close()


def test_affordable_quantity_is_computed_without_incremental_shrinking():
    affordable = getattr(sim_engine, "_affordable_qty", None)
    assert callable(affordable)
    if not affordable:
        return

    assert affordable(1_000, 100, "TW") == 9
    assert affordable(1_000, 3, "US") == 333.33
    us_qty = affordable(1_000, 3, "US")
    assert us_qty * 3 + calc_fee("US", "buy", us_qty * 3) <= 1_000


def test_multiple_buy_signals_reserve_cash_by_confidence():
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "US")
        for order in db.execute(
            __import__("sqlalchemy").select(SimOrder).where(
                SimOrder.account_id == account.id
            )
        ).scalars():
            db.delete(order)
        account.cash = account.initial_cash
        db.commit()

        stocks = []
        for index in range(6):
            stock = _seed_stock(db, f"CASH{index + 1}", market="US")
            _add_report(db, stock, action="buy", confidence=0.95 - index * 0.01)
            stocks.append(stock)

        result = run_decisions(db, "US")
        created_symbols = {order["symbol"] for order in result["orders"]}
        pending = db.execute(
            __import__("sqlalchemy").select(SimOrder).where(
                SimOrder.account_id == account.id, SimOrder.status == "pending"
            )
        ).scalars().all()
        reserved = sum(float(order.qty) * 100 for order in pending)

        assert reserved <= float(account.initial_cash) * (1 - 0.10)
        assert "CASH1" in created_symbols
        assert "CASH6" not in created_symbols
    finally:
        db.close()
