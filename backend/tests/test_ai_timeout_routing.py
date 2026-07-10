import httpx
import pytest

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.providers.ai import antigravity, router
from app.providers.ai.antigravity import AntigravityProvider
from app.providers.ai.gemini import GeminiProvider
from app.providers.ai.schemas import AnalysisReport


async def test_gemini_read_timeout_becomes_fallback_eligible(monkeypatch):
    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("upstream stalled")

    monkeypatch.setattr("app.providers.ai.gemini.httpx.AsyncClient", lambda **kw: TimeoutClient())
    db = SessionLocal()
    try:
        provider = GeminiProvider("gemini-3.5-flash", db)
        with pytest.raises(UpstreamError, match="逾時"):
            await provider._call_api("prompt", AnalysisReport)
    finally:
        db.close()


async def test_antigravity_poll_retries_a_read_timeout(monkeypatch):
    calls = 0

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"id": "job-1", "status": "completed", "output_text": "done"}

    class FlakyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("poll stalled")
            return Response()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(antigravity.httpx, "AsyncClient", lambda **kw: FlakyClient())
    monkeypatch.setattr(antigravity.asyncio, "sleep", no_sleep)
    db = SessionLocal()
    try:
        result = await AntigravityProvider(db)._wait({"id": "job-1", "status": "in_progress"})
    finally:
        db.close()

    assert result["status"] == "completed"
    assert calls == 2


async def test_trading_analysis_prefers_gemini_35(monkeypatch):
    used_models = []
    sentinel = object()

    class FakeProvider:
        def __init__(self, model, db, use_schema=True):
            used_models.append(model)

        async def analyze_batch(self, contexts):
            return sentinel

    monkeypatch.setattr(router, "GeminiProvider", FakeProvider)
    analyze = getattr(router, "analyze_trading_batch", None)
    assert callable(analyze)
    if not analyze:
        return

    result, model = await analyze(object(), [])

    assert result is sentinel
    assert model == "gemini-3.5-flash"
    assert used_models == ["gemini-3.5-flash"]


async def test_daily_briefing_prefers_gemini_35_then_falls_back(monkeypatch):
    used_models = []

    class FakeProvider:
        def __init__(self, model, db, use_schema=True):
            self.model = model
            used_models.append(model)

        async def generate(self, prompt, output_model):
            if self.model == "gemini-3.5-flash":
                raise UpstreamError("timeout")
            return "fallback-result"

    monkeypatch.setattr(router, "GeminiProvider", FakeProvider)
    generate = getattr(router, "generate_premium_structured", None)
    assert callable(generate)
    if not generate:
        return

    result, model = await generate(object(), "prompt", object)

    assert result == "fallback-result"
    assert model == "gemini-3.1-flash-lite"
    assert used_models[:2] == ["gemini-3.5-flash", "gemini-3.1-flash-lite"]
