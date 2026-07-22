"""同步後的新鮮度閘門：資料沒推進到最近已收盤 session 時必須明確失敗。

背景：上游（FinMind 美股日線）在收盤後數小時才就緒，早排的同步每檔都
回傳 0 筆卻仍回報 succeeded，行情因此停在前一日而無人察覺。

測試用獨立的市場代碼 XT，避免與其他測試共用 TW/US 的自選股資料
（本閘門查的是整個市場的 max(date)，共用會互相污染）。
"""
from datetime import date, timedelta

import pytest

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.models import DailyPrice, Stock, WatchlistItem
from app.scheduler import jobs

MARKET = "XT"
SESSION = date(2026, 7, 21)


@pytest.fixture
def _stub_market(monkeypatch):
    """略過交易日曆與實際抓取，只驗證新鮮度判定本身。"""
    monkeypatch.setattr(jobs, "_non_trading_gate", lambda market: None)
    monkeypatch.setattr(
        "app.services.sim.decision._latest_session", lambda market: SESSION
    )

    async def noop_sync(stock_id, market, symbol):
        return 0

    monkeypatch.setattr(jobs, "sync_prices", noop_sync)


def _seed(db, symbol, last_date):
    stock = Stock(symbol=symbol, market=MARKET, name=symbol,
                  currency="USD", kind="stock")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    db.add(DailyPrice(stock_id=stock.id, date=last_date, open=10, high=11,
                      low=9, close=10, volume=100))
    db.add(WatchlistItem(stock_id=stock.id, ai_managed=False))
    db.commit()
    return stock


async def test_sync_fails_when_prices_did_not_reach_latest_session(
    client, _stub_market
):
    db = SessionLocal()
    try:
        _seed(db, "XT901", SESSION - timedelta(days=1))  # 停在前一日
        with pytest.raises(UpstreamError, match="仍停在"):
            await jobs.sync_market_daily(MARKET)
    finally:
        db.close()


async def test_sync_succeeds_when_prices_reached_latest_session(
    client, _stub_market
):
    db = SessionLocal()
    try:
        _seed(db, "XT902", SESSION)
        result = await jobs.sync_market_daily(MARKET)
        assert result["latest_price_date"] == SESSION.isoformat()
        assert result["expected_session"] == SESSION.isoformat()
        assert result["rows_changed"] == 0
    finally:
        db.close()


async def test_sync_skips_on_non_trading_day(client, monkeypatch):
    monkeypatch.setattr(jobs, "is_trading_day", lambda m, d: False)
    result = await jobs.sync_market_daily("TW")
    assert "skipped" in result
