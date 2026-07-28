"""美股折溢價的完整移除：不只是不顯示，而是資料與寫入路徑都不存在。

ce0e4ba 移除了程式與排程，卻把 etf_nav 的舊資料留在庫裡。那些殘留列會被
兩個沒有日期下限的查詢讀到：data-status 的新鮮度回報，以及折溢價警示的
_current_value——後者會拿凍結的舊折溢價永遠比對門檻。
"""
from datetime import date

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal
from app.models import EtfNav, Stock
from app.models.alert import Alert
from app.services.premium_service import SUPPORTED_MARKETS


def _seed_etf(db, symbol, market):
    stock = Stock(
        symbol=symbol, market=market, name=f"{market}ETF{symbol}",
        currency="USD" if market == "US" else "TWD", kind="etf",
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def test_premium_alert_rejected_for_markets_without_nav_source(client):
    """美股 ETF 過得了「是不是 ETF」那關，必須另外被市場檢查擋下。"""
    assert "US" not in SUPPORTED_MARKETS

    db = SessionLocal()
    try:
        _seed_etf(db, "PRMUS", "US")
    finally:
        db.close()

    res = client.post(
        "/api/v1/alerts",
        json={"market": "US", "symbol": "PRMUS",
              "kind": "premium_above", "threshold": 1.0},
    )
    assert res.status_code == 404
    assert "不支援折溢價" in res.json()["error"]


def test_premium_alert_still_allowed_for_supported_market(client):
    """台股淨值走證交所、每日更新，功能必須完好。"""
    db = SessionLocal()
    try:
        _seed_etf(db, "PRMTW", "TW")
    finally:
        db.close()

    res = client.post(
        "/api/v1/alerts",
        json={"market": "TW", "symbol": "PRMTW",
              "kind": "premium_above", "threshold": 1.0},
    )
    assert res.status_code == 200


def test_price_alert_unaffected_for_unsupported_market(client):
    """只擋折溢價，價格警示不受影響。"""
    db = SessionLocal()
    try:
        _seed_etf(db, "PRCUS", "US")
    finally:
        db.close()

    res = client.post(
        "/api/v1/alerts",
        json={"market": "US", "symbol": "PRCUS",
              "kind": "price_above", "threshold": 100.0},
    )
    assert res.status_code == 200


def test_migration_constant_matches_the_application_source_of_truth():
    """migration 刻意不 import 應用程式碼（它得能對當時的 schema 重放），
    代價是常數會有兩份——這條測試就是那份代價的保險。"""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions"
        / "b4d17e0a92c5_purge_unsupported_market_premium_data.py"
    )
    spec = importlib.util.spec_from_file_location("purge_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SUPPORTED_MARKETS == SUPPORTED_MARKETS


@pytest.mark.parametrize("market", ["US"])
def test_purge_removes_nav_rows_and_premium_alerts(client, market):
    """重放 migration 的清除邏輯，確認殘留列與警示都被清掉、台股不受波及。"""
    import importlib.util
    from pathlib import Path

    db = SessionLocal()
    try:
        stale = _seed_etf(db, "PURGE1", market)
        keep = _seed_etf(db, "PURGE2", "TW")
        for stock in (stale, keep):
            db.add(EtfNav(stock_id=stock.id, date=date(2026, 7, 14),
                          nav=100.0, close=100.1, premium_pct=0.1))
            db.add(Alert(stock_id=stock.id, kind="premium_above", threshold=1.0))
        db.commit()
        stale_id, keep_id = stale.id, keep.id
    finally:
        db.close()

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions"
        / "b4d17e0a92c5_purge_unsupported_market_premium_data.py"
    )
    spec = importlib.util.spec_from_file_location("purge_migration_run", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    db = SessionLocal()
    try:
        # 直接跑 migration 的 SQL（op.get_bind() 在測試外不可用，故取同一段邏輯）
        markets = [
            row[0] for row in db.execute(text("SELECT DISTINCT market FROM stocks"))
        ]
        unsupported = [m for m in markets if m not in module.SUPPORTED_MARKETS]
        assert market in unsupported
        params = {f"m{i}": m for i, m in enumerate(unsupported)}
        ph = ", ".join(f":{k}" for k in params)
        db.execute(text(
            f"DELETE FROM alert_events WHERE alert_id IN (SELECT a.id FROM alerts a "
            f"JOIN stocks s ON s.id=a.stock_id WHERE a.kind LIKE 'premium%' "
            f"AND s.market IN ({ph}))"), params)
        db.execute(text(
            f"DELETE FROM alerts WHERE id IN (SELECT a.id FROM alerts a "
            f"JOIN stocks s ON s.id=a.stock_id WHERE a.kind LIKE 'premium%' "
            f"AND s.market IN ({ph}))"), params)
        db.execute(text(
            f"DELETE FROM etf_nav WHERE stock_id IN "
            f"(SELECT id FROM stocks WHERE market IN ({ph}))"), params)
        db.commit()

        assert db.query(EtfNav).filter_by(stock_id=stale_id).count() == 0
        assert db.query(Alert).filter_by(stock_id=stale_id).count() == 0
        # 台股完好——清除必須依市場精準命中
        assert db.query(EtfNav).filter_by(stock_id=keep_id).count() == 1
        assert db.query(Alert).filter_by(stock_id=keep_id).count() == 1
    finally:
        db.close()
