import inspect

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.providers.ai import antigravity, gemini, router
from app.providers.ai.antigravity import AntigravityProvider
from app.providers.ai.gemini import GeminiProvider
from app.providers.ai.schemas import AnalysisReport
from app.models.analysis import AiQuotaReservation, AiUsageLog


@pytest.fixture(autouse=True)
def _isolate_ai_usage():
    models = [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        antigravity.AGENT_ID,
    ]
    db = SessionLocal()
    db.execute(delete(AiUsageLog).where(AiUsageLog.model.in_(models)))
    db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model.in_(models)))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(delete(AiUsageLog).where(AiUsageLog.model.in_(models)))
    db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model.in_(models)))
    db.commit()
    db.close()


async def test_gemini_read_timeout_becomes_fallback_eligible(monkeypatch):
    timeouts = []

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("upstream stalled")

    def client_factory(**kwargs):
        timeouts.append(kwargs["timeout"])
        return TimeoutClient()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.providers.ai.gemini.httpx.AsyncClient", client_factory)
    monkeypatch.setattr(gemini, "_sleep", no_sleep, raising=False)
    monkeypatch.setattr(gemini, "_retry_delay", lambda _retry: 0, raising=False)
    db = SessionLocal()
    try:
        provider = GeminiProvider("gemini-3.6-flash", db)
        with pytest.raises(UpstreamError, match="timed out after 3 attempts"):
            await provider._call_api("prompt", AnalysisReport)
    finally:
        db.close()

    assert len(timeouts) == 3
    assert all(timeout.read == 300 for timeout in timeouts)


async def test_gemini_provider_always_uses_native_response_schema(monkeypatch):
    captured_body = None

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "usageMetadata": {},
                "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
            }

    class CapturingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            nonlocal captured_body
            captured_body = kwargs["json"]
            return Response()

    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: CapturingClient())
    db = SessionLocal()
    try:
        await GeminiProvider("gemini-3.5-flash-lite", db)._call_api(
            "prompt", AnalysisReport
        )
    finally:
        db.close()

    assert "use_schema" not in inspect.signature(GeminiProvider).parameters
    assert "responseSchema" in captured_body["generationConfig"]
    assert captured_body["systemInstruction"]["parts"][0]["text"] == gemini.SYSTEM_PROMPT
    assert captured_body["contents"][0]["parts"][0]["text"] == "prompt"


async def test_gemini_timeout_retries_then_succeeds(monkeypatch):
    calls = 0
    sleeps = []

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
            }

    class FlakyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("upstream stalled")
            return Response()

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: FlakyClient())
    monkeypatch.setattr(gemini, "_sleep", record_sleep, raising=False)
    monkeypatch.setattr(gemini, "_retry_delay", lambda retry: retry + 1, raising=False)
    db = SessionLocal()
    try:
        result = await GeminiProvider("gemini-3.6-flash", db)._call_api(
            "prompt", AnalysisReport
        )
    finally:
        db.close()

    assert result == '{"ok": true}'
    assert calls == 2
    assert sleeps == [1]


async def test_gemini_503_retries_then_succeeds(monkeypatch):
    statuses = [503, 200]
    sleeps = []

    class Response:
        text = "temporarily unavailable"

        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {
                "usageMetadata": {},
                "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
            }

    class FlakyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response(statuses.pop(0))

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: FlakyClient())
    monkeypatch.setattr(gemini, "_sleep", record_sleep, raising=False)
    monkeypatch.setattr(gemini, "_retry_delay", lambda retry: retry + 1, raising=False)
    db = SessionLocal()
    try:
        result = await GeminiProvider("gemini-3.6-flash", db)._call_api(
            "prompt", AnalysisReport
        )
    finally:
        db.close()

    assert result == "{}"
    assert statuses == []
    assert sleeps == [1]


def test_gemini_retry_delay_is_exponential_with_bounded_jitter(monkeypatch):
    retry_delay = getattr(gemini, "_retry_delay", None)
    assert callable(retry_delay)
    monkeypatch.setattr(gemini.random, "uniform", lambda low, high: 0.25)

    assert retry_delay(0) == 1.25
    assert retry_delay(1) == 2.25


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


async def test_gemini_timeout_is_counted_and_releases_reservation(monkeypatch):
    model = "gemini-3.6-flash"

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("upstream stalled")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.providers.ai.gemini.httpx.AsyncClient", lambda **kw: TimeoutClient())
    monkeypatch.setattr(gemini, "_sleep", no_sleep, raising=False)
    monkeypatch.setattr(gemini, "_retry_delay", lambda _retry: 0, raising=False)
    db = SessionLocal()
    try:
        db.execute(delete(AiUsageLog).where(AiUsageLog.model == model))
        db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model == model))
        db.commit()
        with pytest.raises(UpstreamError):
            await GeminiProvider(model, db)._call_api("prompt", AnalysisReport)

        usage = db.execute(
            select(func.count()).select_from(AiUsageLog).where(AiUsageLog.model == model)
        ).scalar_one()
        active = db.execute(
            select(func.count())
            .select_from(AiQuotaReservation)
            .where(AiQuotaReservation.model == model)
        ).scalar_one()
        assert usage == 3
        assert active == 0
    finally:
        db.execute(delete(AiUsageLog).where(AiUsageLog.model == model))
        db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model == model))
        db.commit()
        db.close()
async def test_gemini_timeout_log_contains_render_diagnostics(monkeypatch, caplog):
    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("upstream stalled")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: TimeoutClient())
    monkeypatch.setattr(gemini, "_sleep", no_sleep, raising=False)
    monkeypatch.setattr(gemini, "_retry_delay", lambda _retry: 0, raising=False)
    db = SessionLocal()
    try:
        with caplog.at_level("WARNING", logger="app.providers.ai.gemini"):
            with pytest.raises(UpstreamError):
                await GeminiProvider("gemini-3.6-flash", db)._call_api(
                    "prompt", AnalysisReport
                )
    finally:
        db.close()

    combined = "\n".join(caplog.messages)
    assert "model=gemini-3.6-flash" in combined
    assert "attempt=1/3" in combined
    assert "prompt_chars=6" in combined
    assert "elapsed_ms=" in combined
    assert "status=timeout" in combined


async def test_routine_chain_stops_after_single_model_failure(monkeypatch, caplog):
    used_models = []

    class FakeProvider:
        def __init__(self, model, db):
            self.model = model
            used_models.append(model)

        async def analyze_batch(self, contexts):
            raise UpstreamError("timed out after 3 attempts")

    monkeypatch.setattr(router, "GeminiProvider", FakeProvider)
    with caplog.at_level("WARNING", logger="app.providers.ai.router"):
        with pytest.raises(UpstreamError, match="所有例行分析模型皆不可用"):
            await router.analyze_batch(object(), [])

    assert router.ROUTINE_CHAIN == ["gemini-3.5-flash-lite"]
    assert used_models == ["gemini-3.5-flash-lite"]
    assert any(
        "AI provider failed model=gemini-3.5-flash-lite" in message
        and "error=timed out after 3 attempts" in message
        and "no models remaining" in message
        for message in caplog.messages
    )


async def test_antigravity_deadline_counts_http_timeout_wall_clock(monkeypatch):
    now = 0.0
    calls = 0

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            nonlocal now, calls
            calls += 1
            now += 30
            raise httpx.ReadTimeout("poll stalled")

    async def advance(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(antigravity.httpx, "AsyncClient", lambda **kw: TimeoutClient())
    monkeypatch.setattr(antigravity.asyncio, "sleep", advance)
    monkeypatch.setattr(antigravity, "monotonic", lambda: now, raising=False)
    monkeypatch.setattr(antigravity, "MAX_WAIT_SEC", 10)
    db = SessionLocal()
    try:
        with pytest.raises(UpstreamError, match="逾時"):
            await AntigravityProvider(db)._wait(
                {"id": "job-deadline", "status": "in_progress"}
            )
        assert calls == 1
    finally:
        db.close()


async def test_antigravity_create_timeout_is_counted(monkeypatch):
    model = antigravity.AGENT_ID

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ConnectTimeout("create stalled")

    monkeypatch.setattr(antigravity.httpx, "AsyncClient", lambda **kw: TimeoutClient())
    db = SessionLocal()
    try:
        db.execute(delete(AiUsageLog).where(AiUsageLog.model == model))
        db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model == model))
        db.commit()
        with pytest.raises(UpstreamError):
            await AntigravityProvider(db).research_news("2330", "台積電", "TW")

        assert db.execute(
            select(func.count()).select_from(AiUsageLog).where(AiUsageLog.model == model)
        ).scalar_one() == 1
        assert db.execute(
            select(func.count())
            .select_from(AiQuotaReservation)
            .where(AiQuotaReservation.model == model)
        ).scalar_one() == 0
    finally:
        db.execute(delete(AiUsageLog).where(AiUsageLog.model == model))
        db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model == model))
        db.commit()
        db.close()

async def test_trading_analysis_prefers_gemini_35(monkeypatch):
    used_models = []
    sentinel = object()

    class FakeProvider:
        def __init__(self, model, db):
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
    assert model == "gemini-3.6-flash"
    assert used_models == ["gemini-3.6-flash"]


async def test_daily_briefing_prefers_gemini_35_then_falls_back(monkeypatch):
    used_models = []

    class FakeProvider:
        def __init__(self, model, db):
            self.model = model
            used_models.append(model)

        async def generate(self, prompt, output_model):
            if self.model == "gemini-3.6-flash":
                raise UpstreamError("timeout")
            return "fallback-result"

    monkeypatch.setattr(router, "GeminiProvider", FakeProvider)
    generate = getattr(router, "generate_premium_structured", None)
    assert callable(generate)
    if not generate:
        return

    result, model = await generate(object(), "prompt", object)

    assert result == "fallback-result"
    assert model == "gemini-3.5-flash-lite"
    assert used_models[:2] == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
