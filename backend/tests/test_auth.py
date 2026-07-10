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
