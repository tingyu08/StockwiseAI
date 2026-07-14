from types import SimpleNamespace

from app.core import db as db_module
from app.core.config import Settings


def test_postgres_pool_has_capacity_for_worker_heartbeat_and_dashboard(monkeypatch):
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://user:password@example.invalid/database",
            database_pool_size=5,
            database_max_overflow=5,
            database_pool_timeout=10,
        ),
    )

    engine = db_module._build_engine()
    try:
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 5
        assert engine.pool._timeout == 10
    finally:
        engine.dispose()


def test_gemini_resilience_settings_defaults():
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        finmind_token="test-token",
    )

    assert settings.gemini_read_timeout_seconds == 300
    assert settings.gemini_max_retries == 2
