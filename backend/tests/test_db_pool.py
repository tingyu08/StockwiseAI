from types import SimpleNamespace

from app.core import db as db_module


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
