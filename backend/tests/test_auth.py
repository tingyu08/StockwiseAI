import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.core.logging_config import redact_sensitive
from app.main import app
from app.models import User, UserSession


@pytest.fixture(autouse=True)
def clean_auth():
    with SessionLocal() as db:
        db.query(UserSession).delete()
        db.query(User).delete()
        db.commit()
    yield
    get_settings.cache_clear()


def register(client, username="owner", password="x"):
    client.cookies.clear()
    return client.post("/api/v1/auth/register", json={"username": username, "password": password})


def test_first_user_can_register_with_one_character_password(client):
    assert client.get("/api/v1/auth/session").json()["data"] == {"authenticated": False, "registration_open": True, "username": None}
    response = register(client)
    assert response.status_code == 200
    assert client.get("/api/v1/usage").status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert any("stockwise_session=" in item and "HttpOnly" in item and "SameSite=lax" in item for item in cookies)
    with SessionLocal() as db:
        user = db.scalar(select(User))
        assert user.password_hash != "x"
        assert user.password_hash.startswith("$argon2id$")


def test_registration_closes_after_first_account(client):
    assert register(client).status_code == 200
    client.cookies.clear()
    assert client.post("/api/v1/auth/register", json={"username": "second", "password": "x"}).status_code == 409


def test_empty_credentials_are_rejected(client):
    assert client.post("/api/v1/auth/register", json={"username": " ", "password": "x"}).status_code == 422
    assert client.post("/api/v1/auth/register", json={"username": "owner", "password": ""}).status_code == 422


def test_login_logout_and_session_revocation(client):
    assert register(client, "Owner", "secret").status_code == 200
    csrf = client.cookies.get("stockwise_csrf")
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/v1/usage").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "owner", "password": "secret"}).status_code == 200


def test_login_attempts_are_rate_limited(client):
    assert register(client, "rateowner", "correct").status_code == 200
    client.cookies.clear()
    with TestClient(app, client=("rate-limit-test", 50000)) as rate_client:
        responses = [rate_client.post("/api/v1/auth/login", json={"username": "rateowner", "password": "wrong"}) for _ in range(6)]
    assert responses[-1].status_code == 429
    assert int(responses[-1].headers["Retry-After"]) > 0


def test_health_public_but_private_api_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/usage").status_code == 401


def test_production_requires_only_job_secret():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", gemini_api_key="g", finmind_token="f", job_token="")
    settings = Settings(_env_file=None, environment="production", gemini_api_key="g", finmind_token="f", job_token="scheduler-secret")
    assert settings.job_token == "scheduler-secret"


def test_sensitive_configuration_values_are_redacted_from_logs():
    settings = Settings(_env_file=None, gemini_api_key="gemini-private-key", finmind_token="finmind-private-token", job_token="job-private-token")
    assert redact_sensitive("keys: gemini-private-key job-private-token", settings) == "keys: [REDACTED] [REDACTED]"
