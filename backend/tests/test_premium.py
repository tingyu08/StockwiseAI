"""折溢價：僅台股支援，且上游整批失敗不得偽裝成功（曾無聲停更一週）。"""
from datetime import date

import pytest

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.models import EtfNav, Stock
from app.services.premium_service import SUPPORTED_MARKETS, snapshot_premiums


def _seed_etf(db, symbol, market="TW"):
    stock = Stock(
        symbol=symbol, market=market, name=f"測試{symbol}",
        currency="TWD" if market == "TW" else "USD", kind="etf",
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def test_only_taiwan_is_supported():
    assert SUPPORTED_MARKETS == ("TW",)


async def test_us_snapshot_is_skipped_not_attempted(client):
    """美股不再抓淨值：直接跳過，不得因此讓工作失敗。"""
    db = SessionLocal()
    try:
        _seed_etf(db, "ZVOO", market="US")
        result = await snapshot_premiums(db, "US")
        assert result["updated"] == 0
        assert "skipped" in result
    finally:
        db.close()


async def test_snapshot_raises_when_all_etfs_fail(client, monkeypatch):
    db = SessionLocal()
    try:
        _seed_etf(db, "0099A")

        async def empty(_symbols):
            return {}

        monkeypatch.setattr("app.services.premium_service._tw_snapshot", empty)
        with pytest.raises(UpstreamError, match="零更新"):
            await snapshot_premiums(db, "TW")
    finally:
        db.close()


async def test_snapshot_succeeds_with_partial_rows(client, monkeypatch):
    db = SessionLocal()
    try:
        etf = _seed_etf(db, "0098A")
        _seed_etf(db, "0097A")  # 這檔抓不到 → 部分成功仍算成功

        async def partial(_symbols):
            return {"0098A": (100.0, 101.0, date(2026, 7, 22))}

        monkeypatch.setattr("app.services.premium_service._tw_snapshot", partial)
        result = await snapshot_premiums(db, "TW")

        assert result["updated"] == 1
        row = db.query(EtfNav).filter(EtfNav.stock_id == etf.id).one()
        assert float(row.premium_pct) == pytest.approx(1.0)
    finally:
        db.close()
