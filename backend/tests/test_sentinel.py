"""盤中出場哨兵與交易日/新鮮度閘門的測試。"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import DailyPrice, SimOrder
from app.services.sim.decision import run_decisions
from app.services.sim.engine import calc_fee, get_or_create_account
from app.services.sim.portfolio import current_positions
from app.services.sim.sentinel import run_exit_sentinel
from app.services.trading_calendar import is_trading_day, last_trading_session
from tests.test_simulation import _add_report, _seed_stock


def _seed_position(db, symbol, market="TW", entry_price=100.0, qty=100.0):
    """建立持倉：filled 買單＋附 stop_loss=80 / target_price_high=120 的報告。"""
    stock = _seed_stock(db, symbol, market=market)
    report = _add_report(db, stock, action="buy", confidence=0.9, stop_loss=80.0)
    account = get_or_create_account(db, market)
    db.add(SimOrder(
        account_id=account.id, stock_id=stock.id, side="buy", qty=qty,
        fill_price=entry_price, fee=calc_fee(market, "buy", qty * entry_price),
        status="filled", decided_by="ai", ai_report_id=report.id,
        filled_at=datetime.now() - timedelta(days=5),
    ))
    db.commit()
    return stock, account


@pytest.fixture
def _open_market(monkeypatch):
    monkeypatch.setattr("app.services.sim.sentinel.is_trading_day", lambda m, d: True)
    monkeypatch.setattr("app.services.sim.sentinel._in_market_hours", lambda m: True)


def _patch_quotes(monkeypatch, quotes: dict[str, float]):
    async def fake(market, symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    monkeypatch.setattr("app.services.sim.sentinel.fetch_intraday_quotes", fake)


# ---- 哨兵觸發 ----

async def test_sentinel_stop_loss_exit(client, monkeypatch, _open_market):
    db = SessionLocal()
    try:
        stock, account = _seed_position(db, "9201")
        _patch_quotes(monkeypatch, {"9201": 75.0})  # < stop 80
        cash_before = float(account.cash)

        result = await run_exit_sentinel(db, "TW")

        assert len(result["exits"]) == 1
        exit_ = result["exits"][0]
        assert exit_["kind"] == "stop_loss" and exit_["price"] == 75.0
        assert current_positions(db, account).get(stock.id) is None
        gross = 100.0 * 75.0
        db.refresh(account)
        assert float(account.cash) == pytest.approx(
            cash_before + gross - calc_fee("TW", "sell", gross), abs=0.01
        )
        order = db.execute(
            __import__("sqlalchemy").select(SimOrder).where(
                SimOrder.stock_id == stock.id, SimOrder.side == "sell"
            )
        ).scalar_one()
        assert order.status == "filled" and order.fill_kind == "stop_loss"
    finally:
        db.close()


async def test_sentinel_take_profit_exit(client, monkeypatch, _open_market):
    db = SessionLocal()
    try:
        stock, account = _seed_position(db, "9202")
        _patch_quotes(monkeypatch, {"9202": 125.0})  # > target 120

        result = await run_exit_sentinel(db, "TW")

        assert [e["kind"] for e in result["exits"]] == ["take_profit"]
        assert current_positions(db, account).get(stock.id) is None
    finally:
        db.close()


async def test_sentinel_no_action_between_levels(client, monkeypatch, _open_market):
    db = SessionLocal()
    try:
        stock, account = _seed_position(db, "9203")
        _patch_quotes(monkeypatch, {"9203": 100.0})  # 80 < 100 < 120

        result = await run_exit_sentinel(db, "TW")

        assert result["exits"] == []
        assert current_positions(db, account).get(stock.id) == 100.0
    finally:
        db.close()


async def test_sentinel_skips_when_pending_order_exists(client, monkeypatch, _open_market):
    db = SessionLocal()
    try:
        stock, account = _seed_position(db, "9204")
        db.add(SimOrder(
            account_id=account.id, stock_id=stock.id, side="sell", qty=100.0,
            status="pending", decided_by="ai",
        ))
        db.commit()
        _patch_quotes(monkeypatch, {"9204": 75.0})

        result = await run_exit_sentinel(db, "TW")

        assert result["exits"] == []  # 讓既有 pending 流程處理，不重複下單
    finally:
        db.close()


async def test_sentinel_noop_on_non_trading_day(client, monkeypatch):
    monkeypatch.setattr("app.services.sim.sentinel.is_trading_day", lambda m, d: False)
    db = SessionLocal()
    try:
        result = await run_exit_sentinel(db, "TW")
        assert result["skipped"] == "非交易日"
    finally:
        db.close()


# ---- 健康檢查需支援 HEAD（uptime 監測服務的預設探測方法）----

def test_health_endpoints_accept_head(client):
    assert client.head("/api/v1/health/live").status_code == 200
    assert client.head("/api/v1/health").status_code == 200


# ---- external 模式的哨兵專用排程器 ----

async def test_sentinel_scheduler_registers_only_sentinels():
    from app.scheduler.jobs import start_sentinel_scheduler

    scheduler = start_sentinel_scheduler()
    try:
        jobs = scheduler.get_jobs()
        assert len(jobs) == 3  # TW 每小時、US 每小時、US 收盤前最後一巡
        # 內部排程走 JobRun 佇列（工作中心可見＋與 GH 備援去重）
        assert all(job.func.__name__ == "_enqueue_scheduled" for job in jobs)
        assert sorted(job.args[0] for job in jobs) == ["sentinel-tw", "sentinel-us", "sentinel-us"]
    finally:
        scheduler.shutdown(wait=False)


# ---- 交易日曆 ----

def test_calendar_known_dates():
    assert is_trading_day("TW", date(2026, 1, 1)) is False  # 元旦
    assert is_trading_day("TW", date(2026, 7, 15)) is True  # 週三
    assert is_trading_day("US", date(2026, 7, 4)) is False  # 週六（美國國慶）
    # 週日 → 回推到最近的週五
    assert last_trading_session("TW", date(2026, 7, 12)) == date(2026, 7, 10)
    assert last_trading_session("US", date(2026, 7, 15)) == date(2026, 7, 15)


# ---- 開盤前決策 → 當日開盤成交（引擎語意）----

def test_pre_open_order_fills_at_same_day_open(client):
    from app.services.sim.engine import fill_pending_orders

    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9301")
        account = get_or_create_account(db, "TW")
        last_price = db.execute(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one()
        # 委託建立於「最後價格日」台灣時間 07:30（開盤前）→ 應吃同一天的開盤價
        pre_open_local = datetime(
            last_price.date.year, last_price.date.month, last_price.date.day,
            7, 30, tzinfo=ZoneInfo("Asia/Taipei"),
        )
        order = SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=10,
            status="pending", decided_by="ai",
            created_at=pre_open_local.astimezone(timezone.utc).replace(tzinfo=None),
        )
        db.add(order)
        db.commit()

        result = fill_pending_orders(db, "TW")

        db.refresh(order)
        assert result["filled"] >= 1
        assert order.status == "filled"
        assert float(order.fill_price) == float(last_price.open)  # 當日開盤，非隔日
    finally:
        db.close()


def test_post_open_order_waits_for_next_session(client):
    from app.services.sim.engine import fill_pending_orders

    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9302")
        account = get_or_create_account(db, "TW")
        last_price = db.execute(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one()
        # 委託建立於「最後價格日」15:00（開盤後）→ 需要下一交易日資料，只能等待
        post_open_local = datetime(
            last_price.date.year, last_price.date.month, last_price.date.day,
            15, 0, tzinfo=ZoneInfo("Asia/Taipei"),
        )
        order = SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=10,
            status="pending", decided_by="ai",
            created_at=post_open_local.astimezone(timezone.utc).replace(tzinfo=None),
        )
        db.add(order)
        db.commit()

        fill_pending_orders(db, "TW")

        db.refresh(order)
        assert order.status == "pending"  # 最後價格日之後沒有資料 → 等待
    finally:
        db.close()


# ---- 已收盤 session 判定（晨間決策的新鮮度基準）----

def test_latest_session_pre_open_uses_previous_session():
    from app.services.sim.decision import _latest_session

    tw = ZoneInfo("Asia/Taipei")
    # 2026-07-15（週三）07:30 開盤前 → 已收盤 session 是 07-14（週二）
    pre_open = datetime(2026, 7, 15, 7, 30, tzinfo=tw).astimezone(timezone.utc)
    assert _latest_session("TW", pre_open) == date(2026, 7, 14)
    # 同日 14:30 收盤後 → 07-15
    post_close = datetime(2026, 7, 15, 14, 30, tzinfo=tw).astimezone(timezone.utc)
    assert _latest_session("TW", post_close) == date(2026, 7, 15)
    # 週一 07:30 → 上週五
    monday = datetime(2026, 7, 13, 7, 30, tzinfo=tw).astimezone(timezone.utc)
    assert _latest_session("TW", monday) == date(2026, 7, 10)


# ---- 決策端價格新鮮度閘門 ----

def test_decision_skips_stale_prices(client, monkeypatch):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9205")
        _add_report(db, stock, action="buy", confidence=0.9)
        # 最新交易日設為「今天」，但種子價格停在數十天前 → 應跳過
        monkeypatch.setattr(
            "app.services.sim.decision._latest_session", lambda market: date.today()
        )
        result = run_decisions(db, "TW")
        skip = next(s for s in result["skipped"] if s["symbol"] == "9205")
        assert "價格尚未更新" in skip["reason"]
        assert "9205" not in [o["symbol"] for o in result["orders"]]
    finally:
        db.close()
