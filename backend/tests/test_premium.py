"""折溢價快照的失敗可見性：上游整批失敗不得偽裝成功（曾無聲停更一週）。"""
from datetime import date

import pytest

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.models import EtfNav, Stock
from app.services.premium_service import snapshot_premiums


def _seed_etf(db, symbol, market="US"):
    stock = Stock(
        symbol=symbol, market=market, name=f"測試{symbol}",
        currency="USD" if market == "US" else "TWD", kind="etf",
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


async def test_snapshot_raises_when_all_etfs_fail(client, monkeypatch):
    db = SessionLocal()
    try:
        _seed_etf(db, "ZVOO")

        async def empty(_symbols):
            return {}

        monkeypatch.setattr("app.services.premium_service._us_snapshot", empty)
        with pytest.raises(UpstreamError, match="零更新"):
            await snapshot_premiums(db, "US")
    finally:
        db.close()


async def test_snapshot_succeeds_with_partial_rows(client, monkeypatch):
    db = SessionLocal()
    try:
        etf = _seed_etf(db, "ZQQQ")
        _seed_etf(db, "ZSPY")  # 這檔抓不到 → 部分成功仍算成功

        async def partial(_symbols):
            return {"ZQQQ": (100.0, 101.0, date(2026, 7, 21))}

        monkeypatch.setattr("app.services.premium_service._us_snapshot", partial)
        result = await snapshot_premiums(db, "US")

        assert result["updated"] == 1
        row = db.query(EtfNav).filter(EtfNav.stock_id == etf.id).one()
        assert float(row.premium_pct) == pytest.approx(1.0)
    finally:
        db.close()
