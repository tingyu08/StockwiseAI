from pathlib import Path

import yaml
from sqlalchemy.exc import OperationalError

from app.core.db import get_db
from app.main import app


def test_render_health_check_uses_process_liveness():
    render_config = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "render.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert render_config["services"][0]["healthCheckPath"] == "/api/v1/health/live"


def test_health_returns_envelope(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"] == {"status": "ok"}
    assert body["error"] is None


def test_liveness_and_database_readiness_are_separate(client):
    assert client.get("/api/v1/health/live").json()["data"] == {"status": "alive"}
    ready = client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["data"] == {"status": "ready", "database": "ok"}


def test_readiness_returns_503_when_database_is_unavailable(client):
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("offline"))

    def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json()["success"] is False


def test_usage_lists_all_quota_models(client):
    res = client.get("/api/v1/usage")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    models = {row["model"] for row in body["data"]}
    assert "gemini-3.5-flash-lite" in models
    assert "gemini-3.6-flash" in models
    assert "gemma-4-31b-it" not in models
    for row in body["data"]:
        assert row["used"] == 0
        assert row["remaining"] == row["rpd"]


def test_data_status_reports_market_freshness(client):
    response = client.get("/api/v1/data-status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"TW", "US"}
    for market in ("TW", "US"):
        assert set(data[market]) == {
            "latest_price_date",
            "latest_nav_date",
            "latest_ai_runs",
            "latest_overview_run",
            "latest_successful_job",
        }
        assert set(data[market]["latest_ai_runs"]) == {"news", "routine", "trade"}


def test_data_status_hides_nav_for_markets_without_premium_support(client):
    """美股折溢價已下架，但 etf_nav 還留著下架前的舊資料。

    無條件查最大日期會把那個停止更新的日期當成資料新鮮度回報——正式環境
    顯示「NAV 2026-07-14」，看起來像排程壞了。不支援的市場一律回 None。
    """
    from datetime import date

    from app.core.db import SessionLocal
    from app.models import EtfNav, Stock
    from app.services.premium_service import SUPPORTED_MARKETS

    assert "US" not in SUPPORTED_MARKETS  # 前提：美股不支援折溢價

    db = SessionLocal()
    try:
        etf = Stock(symbol="NAVUS", market="US", name="殘留淨值 ETF",
                    currency="USD", kind="etf")
        db.add(etf)
        db.commit()
        db.refresh(etf)
        db.add(EtfNav(stock_id=etf.id, date=date(2026, 7, 14),
                      nav=100.0, close=100.1, premium_pct=0.1))
        db.commit()
    finally:
        db.close()

    data = client.get("/api/v1/data-status").json()["data"]
    assert data["US"]["latest_nav_date"] is None
