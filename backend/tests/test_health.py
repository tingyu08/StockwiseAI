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
            "latest_ai_date",
            "latest_ai_dates",
            "latest_successful_job",
        }
        assert set(data[market]["latest_ai_dates"]) == {"news", "routine", "trade"}
