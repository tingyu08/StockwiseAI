import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    get_settings.cache_clear()


def test_private_api_rejects_missing_bearer_token(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "single-user-secret")
    get_settings.cache_clear()

    response = client.get("/api/v1/usage")

    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "data": None,
        "error": "需要有效的 API Token",
        "meta": None,
    }


def test_private_api_accepts_valid_bearer_token(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "single-user-secret")
    get_settings.cache_clear()

    response = client.get(
        "/api/v1/usage",
        headers={"Authorization": "Bearer single-user-secret"},
    )

    assert response.status_code == 200


def test_health_remains_public_when_api_token_is_configured(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "single-user-secret")
    get_settings.cache_clear()

    response = client.get("/api/v1/health")

    assert response.status_code == 200


def test_job_token_can_poll_job_status(client, monkeypatch):
    from app.services.job_service import enqueue_job

    monkeypatch.setenv("API_TOKEN", "single-user-secret")
    monkeypatch.setenv("JOB_TOKEN", "scheduler-secret")
    get_settings.cache_clear()
    run_id = enqueue_job("auth-poll", payload={"name": "sync-tw"})

    response = client.get(
        f"/api/v1/jobs/runs/{run_id}",
        headers={"X-Job-Token": "scheduler-secret"},
    )

    assert response.status_code == 200
    from app.core.db import SessionLocal
    from app.models import JobRun

    db = SessionLocal()
    try:
        db.delete(db.get(JobRun, run_id))
        db.commit()
    finally:
        db.close()
