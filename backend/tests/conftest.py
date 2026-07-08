import os

# 測試環境變數必須在 import app 之前設定（Settings 為 lru_cache）
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("FINMIND_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

import pytest
from fastapi.testclient import TestClient

from app.core.db import Base, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()
    if os.path.exists("test.db"):
        os.remove("test.db")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
