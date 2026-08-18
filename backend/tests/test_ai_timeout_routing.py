import inspect

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.providers.ai import gemini, router
from app.providers.ai.gemini import GeminiProvider
from app.providers.ai.schemas import AnalysisReport
from app.models.analysis import AiQuotaReservation, AiUsageLog


@pytest.fixture(autouse=True)
def _isolate_ai_usage():
    models = [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
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
    # 503 走專屬的長退避（見 _service_unavailable_delay），不是通用的 _retry_delay
    monkeypatch.setattr(
        gemini, "_service_unavailable_delay", lambda retry: retry + 1, raising=False
    )
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
def test_premium_chain_keeps_a_middle_rung_before_dropping_to_routine():
    """premium 與例行鏈之間必須留一級，不可讓 premium 掛掉就直接掉到 flash-lite。

    3.7-flash 剛推出、供不應求（2026-08-14 實測連打 6 次全數 503／逾時）。
    少了前代這一級，3.7 不可用的期間交易決策與每日簡報會整段用 flash-lite，
    品質反而比換用 3.7 之前更差。等 3.7 穩定後才可拿掉這一級。

    斷言鎖的是「結構」不是版本號：換代時只要仍維持三級就不會壞。
    """
    assert router.PREMIUM_CHAIN[0] == router.PREMIUM_MODEL
    assert router.PREMIUM_CHAIN[1] == router.PREMIUM_FALLBACK_MODEL
    assert router.PREMIUM_FALLBACK_MODEL not in router.ROUTINE_CHAIN
    assert router.PREMIUM_CHAIN[-len(router.ROUTINE_CHAIN):] == router.ROUTINE_CHAIN


async def test_trading_analysis_prefers_the_premium_model(monkeypatch):
    """契約是「優先用 PREMIUM_MODEL」，不是「一定是某個版本號」。

    寫死模型名稱會讓每次換代都無謂地打掛測試（3.6→3.7 時就是如此）。
    """
    used_models = []
    sentinel = object()

    class FakeProvider:
        def __init__(self, model, db, **options):
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
    assert model == router.PREMIUM_MODEL
    assert used_models == [router.PREMIUM_MODEL]


async def test_daily_briefing_falls_through_the_premium_chain(monkeypatch):
    """premium 掛掉時要沿 PREMIUM_CHAIN 逐級降，最後仍產得出簡報。"""
    used_models = []

    class FakeProvider:
        def __init__(self, model, db, **options):
            self.model = model
            used_models.append(model)

        async def generate(self, prompt, output_model):
            # 除了鏈上最後一個（flash-lite）之外全部失敗，驗證逐級降到底
            if self.model != router.ROUTINE_CHAIN[-1]:
                raise UpstreamError("timeout")
            return "fallback-result"

    monkeypatch.setattr(router, "GeminiProvider", FakeProvider)
    generate = getattr(router, "generate_premium_structured", None)
    assert callable(generate)
    if not generate:
        return

    result, model = await generate(object(), "prompt", object)

    assert result == "fallback-result"
    assert model == router.ROUTINE_CHAIN[-1]
    # 依序試過整條鏈，且第一個一定是 premium
    assert used_models == router.PREMIUM_CHAIN
    assert used_models[0] == router.PREMIUM_MODEL
