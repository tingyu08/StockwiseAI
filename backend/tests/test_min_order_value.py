"""委託金額下限：小額單會被手續費吃掉，不該下。

正式環境實例（2026-08-07）：AI 對 00981A 下了 1 股、成交價 28.92，
台股最低手續費 20 元照收——手續費佔成交金額 69%，成本均價被推到 48.92，
一買進就浮虧 41%，與行情無關。同批的 2344（2 股）、2408（1 股）同樣被
費用侵蝕。

成因是 _size_buy 只要算出 qty >= 1 就下單，沒有金額下限：現金快見底時
budget 很小，就會擠出這種註定虧損的單，還佔用現金與持倉名額。

門檻只對台股成立——美股手續費為 0，小額單沒有成本劣勢（見 calc_fee）。
規則在「決策」與「撮合」兩處都要成立：撮合時的縮量會讓已通過決策的單
再度掉到門檻以下，只擋一邊等於留了後門。
"""
from datetime import date, datetime

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import DailyPrice, SimOrder, Stock
from app.services.sim.engine import (
    MIN_ORDER_VALUE,
    fill_pending_orders,
    get_or_create_account,
    meets_min_order_value,
)

SYMBOL_PREFIX = "MV0"


@pytest.fixture(autouse=True)
def _clean_up():
    yield
    db = SessionLocal()
    try:
        ids = [
            row.id
            for row in db.execute(
                select(Stock).where(Stock.symbol.like(f"{SYMBOL_PREFIX}%"))
            ).scalars()
        ]
        if ids:
            db.execute(delete(SimOrder).where(SimOrder.stock_id.in_(ids)))
            db.execute(delete(DailyPrice).where(DailyPrice.stock_id.in_(ids)))
            db.execute(delete(Stock).where(Stock.id.in_(ids)))
            db.commit()
    finally:
        db.close()


def test_threshold_is_ten_thousand_for_tw_only():
    assert MIN_ORDER_VALUE["TW"] == 10_000.0
    # 美股零手續費，小額單不會被費用侵蝕，不設限
    assert MIN_ORDER_VALUE["US"] == 0.0


@pytest.mark.parametrize(
    "gross,allowed",
    [
        (28.92, False),   # 正式環境那筆 00981A
        (9_999.99, False),
        (10_000.0, True),  # 邊界：剛好達標要放行
        (10_000.01, True),
    ],
)
def test_tw_orders_must_reach_the_threshold(gross, allowed):
    assert meets_min_order_value("TW", gross) is allowed


def test_us_orders_are_not_blocked_by_the_tw_threshold():
    assert meets_min_order_value("US", 28.92) is True


def test_threshold_keeps_fees_under_a_fifth_of_a_percent():
    """10,000 的門檻等於把最低手續費壓在 0.2% 以內。

    這個關係才是門檻的意義所在；改動門檻時要一併確認它仍成立。
    """
    from app.services.sim.engine import TW_FEE_MIN, calc_fee

    fee = calc_fee("TW", "buy", MIN_ORDER_VALUE["TW"])
    assert fee == TW_FEE_MIN  # 這個金額仍落在「最低收費」區間
    assert fee / MIN_ORDER_VALUE["TW"] <= 0.002


def _stock_with_price(db, symbol, market, session_day, price):
    stock = Stock(
        symbol=symbol, market=market, name=f"測試{symbol}",
        currency="TWD" if market == "TW" else "USD", kind="stock",
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    db.add(DailyPrice(
        stock_id=stock.id, date=session_day,
        open=price, high=price, low=price, close=price, volume=1000,
    ))
    db.commit()
    return stock


def test_fill_rejects_an_order_that_shrank_below_the_threshold():
    """撮合縮量後掉到門檻以下 → 拒絕，不可讓小額單從這個後門溜進來。

    決策用昨收估價，開盤跳空時 _affordable_qty 會縮量；縮到剩幾股的話，
    成交金額可能只剩幾百元，手續費照樣收 20 元。
    """
    session_day = date(2026, 7, 15)
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        original_cash = float(account.cash)
        stock = _stock_with_price(db, "MV001", "TW", session_day, price=500.0)
        account.cash = 1_500.0  # 只買得起 2 股 ＝ 1,000 元，低於門檻
        order = SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=100,
            status="pending", decided_by="ai",
            created_at=datetime(2026, 7, 14, 23, 10),
        )
        db.add(order)
        db.commit()

        result = fill_pending_orders(db, "TW")

        db.refresh(order)
        assert result["rejected"] == 1
        assert order.status == "rejected"
        assert "10,000" in order.reject_reason or "下限" in order.reject_reason
    finally:
        account = get_or_create_account(db, "TW")
        account.cash = original_cash
        db.commit()
        db.close()


def test_fill_still_accepts_an_order_above_the_threshold():
    """門檻不可誤傷正常委託。"""
    session_day = date(2026, 7, 15)
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        original_cash = float(account.cash)
        stock = _stock_with_price(db, "MV002", "TW", session_day, price=500.0)
        account.cash = 100_000.0
        order = SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=40,  # 20,000
            status="pending", decided_by="ai",
            created_at=datetime(2026, 7, 14, 23, 10),
        )
        db.add(order)
        db.commit()

        fill_pending_orders(db, "TW")

        db.refresh(order)
        assert order.status == "filled"
        assert float(order.qty) * float(order.fill_price) >= 10_000
    finally:
        account = get_or_create_account(db, "TW")
        account.cash = original_cash
        db.commit()
        db.close()


def test_decision_skips_a_candidate_it_can_only_afford_in_dribs(monkeypatch):
    """決策端就要擋下來——這才是那筆 1 股 28.92 元的來源。

    現金見底時 _size_buy 仍會擠出幾股的量；讓它進到 pending 只是把問題
    往後推一天，還白佔一個持倉名額。
    """
    import json

    from app.models import AiReport, WatchlistItem
    from app.services.sim.decision import run_decisions

    monkeypatch.setattr(
        "app.services.sim.decision._latest_session", lambda market: date(2000, 1, 1)
    )
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        original_cash = float(account.cash)
        session_day = date(2026, 7, 15)
        stock = _stock_with_price(db, "MV004", "TW", session_day, price=100.0)
        db.add(WatchlistItem(stock_id=stock.id, ai_managed=True))
        db.add(AiReport(
            stock_id=stock.id, trade_date=session_day, provider="test", model="test",
            prompt_version="v1", kind="routine", action="buy", confidence=0.8,
            payload_json=json.dumps({
                "symbol": stock.symbol, "action": "buy", "confidence": 0.8,
                "target_price_low": 90, "target_price_high": 120, "stop_loss": 80,
                "reasoning": "測試", "risks": [],
                "scenarios": {
                    k: {"target_price": 100, "trigger_condition": "t", "probability": p}
                    for k, p in (("bull", 0.3), ("base", 0.5), ("bear", 0.2))
                },
            }),
        ))
        # 現金只剩 5,000：扣掉保留現金後 budget 約 1,000 → 約 9 股 ＝ 900 元
        account.cash = 5_000.0
        db.commit()

        result = run_decisions(db, "TW")

        assert not any(o["symbol"] == "MV004" for o in result["orders"]), (
            "小額候選仍被下單了"
        )
        reason = next(
            s["reason"] for s in result["skipped"] if s["symbol"] == "MV004"
        )
        assert "下限" in reason, reason
    finally:
        account = get_or_create_account(db, "TW")
        account.cash = original_cash
        db.execute(delete(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
        db.execute(delete(AiReport).where(AiReport.stock_id == stock.id))
        db.commit()
        db.close()


def test_sell_orders_are_never_blocked_by_the_threshold():
    """賣出不受金額下限限制——擋住出場等於把零股鎖死在帳上。

    停損更是如此：剩幾股的殘餘部位若因金額太小而賣不掉，會永遠留著。
    """
    session_day = date(2026, 7, 15)
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock_with_price(db, "MV003", "TW", session_day, price=50.0)
        db.add(SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=2,
            fill_price=50.0, fee=20.0, status="filled", decided_by="ai",
            created_at=datetime(2026, 7, 10, 1, 0),
            filled_at=datetime(2026, 7, 10, 1, 0),
        ))
        db.commit()
        sell = SimOrder(
            account_id=account.id, stock_id=stock.id, side="sell", qty=2,  # 僅 100 元
            status="pending", decided_by="ai",
            created_at=datetime(2026, 7, 14, 23, 10),
        )
        db.add(sell)
        db.commit()

        fill_pending_orders(db, "TW")

        db.refresh(sell)
        assert sell.status == "filled", "賣出被金額下限擋住了"
    finally:
        db.close()
