import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging_config import redact_sensitive


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    get_settings.cache_clear()


def test_browser_api_does_not_require_bearer_token(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "single-user-secret")
    get_settings.cache_clear()

    response = client.get("/api/v1/usage")

    assert response.status_code == 200


def test_health_is_public(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200


def test_production_requires_job_token():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            gemini_api_key="gemini",
            finmind_token="finmind",
            job_token="",
        )

    settings = Settings(
        _env_file=None,
        environment="production",
        gemini_api_key="gemini",
        finmind_token="finmind",
        job_token="scheduler-secret",
    )
    assert settings.job_token == "scheduler-secret"


def test_development_allows_empty_private_tokens():
    settings = Settings(
        _env_file=None,
        environment="development",
        gemini_api_key="gemini",
        finmind_token="finmind",
        job_token="",
        cors_origins=" http://localhost:3000, http://localhost:3001 ",
    )

    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://localhost:3001",
    ]


def test_api_responses_include_security_and_request_id_headers(client):
    response = client.get(
        "/api/v1/health/live", headers={"X-Request-ID": "test-request-123"}
    )

    assert response.headers["X-Request-ID"] == "test-request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_sensitive_configuration_values_are_redacted_from_logs():
    settings = Settings(
        _env_file=None,
        gemini_api_key="gemini-private-key",
        finmind_token="finmind-private-token",
        job_token="job-private-token",
    )

    message = redact_sensitive(
        "keys: gemini-private-key job-private-token", settings
    )
    assert message == "keys: [REDACTED] [REDACTED]"


def test_job_token_can_poll_job_status(client, monkeypatch):
    from app.services.job_service import enqueue_job

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
