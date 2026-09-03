"""503 有備援時不在同一個模型上重試。

Google 的 503 會計入 RPD（2026-08-18 由帳號持有人確認），而 premium 模型的
免費額度只有每日 20 次。原本的行為是同一個模型連打 max_attempts 次才降級
——新發表的 flash 常態性 503（3.7-flash 上線兩週的正式環境實測是 0/5），
等於每個邏輯呼叫燒掉 3 個 RPD 卻一次都沒成功，額度大半付給了 503。

規則：鏈上還有備援的模型，503 就只送一次然後降級（下一級是獨立額度）；
鏈尾沒得降，維持長退避重試。
"""
import pytest
from pydantic import BaseModel
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.providers.ai import gemini, router
from app.providers.ai.gemini import GeminiProvider
from app.providers.ai.schemas import AnalysisReport
from app.models.analysis import AiQuotaReservation, AiUsageLog

MODELS = list(dict.fromkeys(router.PREMIUM_CHAIN))


class Tiny(BaseModel):
    ok: bool


@pytest.fixture(autouse=True)
def _isolate_ai_usage():
    def wipe():
        db = SessionLocal()
        db.execute(delete(AiUsageLog).where(AiUsageLog.model.in_(MODELS)))
        db.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model.in_(MODELS)))
        db.commit()
        db.close()

    wipe()
    yield
    wipe()


class _Response:
    text = "model overloaded"

    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {
            "usageMetadata": {},
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
        }


def _client_factory(status_for, calls):
    """回傳依模型決定狀態碼的假 client，並記下每次呼叫的模型。"""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            model = url.rsplit("/", 1)[-1].split(":")[0]
            calls.append(model)
            return _Response(status_for(model))

    return lambda **kwargs: FakeClient()


def _no_delays(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(gemini, "_sleep", no_sleep, raising=False)
    monkeypatch.setattr(gemini, "_retry_delay", lambda _retry: 0, raising=False)
    monkeypatch.setattr(
        gemini, "_service_unavailable_delay", lambda _retry: 0, raising=False
    )


async def test_premium_503_falls_back_after_a_single_request(monkeypatch):
    """3.7 回 503 只能扣一次額度——重試兩次是把 15% 的當日額度送給故障。"""
    head, second = router.PREMIUM_CHAIN[0], router.PREMIUM_CHAIN[1]
    calls: list[str] = []
    _no_delays(monkeypatch)
    monkeypatch.setattr(
        gemini.httpx,
        "AsyncClient",
        _client_factory(lambda m: 503 if m == head else 200, calls),
    )

    db = SessionLocal()
    try:
        result, model = await router.generate_premium_structured(db, "prompt", Tiny)
    finally:
        db.close()

    assert result.ok is True
    assert model == second
    assert calls.count(head) == 1, (
        f"{head} 有備援卻重試了 {calls.count(head)} 次：{calls}"
    )


async def test_chain_tail_still_retries_503(monkeypatch):
    """flash-lite 是鏈尾，沒有下一級可降——它必須維持退避重試。"""
    calls: list[str] = []
    _no_delays(monkeypatch)
    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", _client_factory(lambda _m: 503, calls)
    )

    db = SessionLocal()
    try:
        with pytest.raises(UpstreamError):
            await router.generate_structured(db, "prompt", Tiny)
    finally:
        db.close()

    assert calls.count("gemini-3.5-flash-lite") == 3, (
        f"鏈尾不該只送一次：{calls}"
    )


async def test_direct_provider_keeps_retrying_by_default(monkeypatch):
    """不經 router 直接建構時維持原行為——降級是 router 的職責，不是 provider 的。"""
    calls: list[str] = []
    _no_delays(monkeypatch)
    monkeypatch.setattr(
        gemini.httpx, "AsyncClient", _client_factory(lambda _m: 503, calls)
    )

    db = SessionLocal()
    try:
        with pytest.raises(UpstreamError):
            await GeminiProvider(router.PREMIUM_MODEL, db)._call_api(
                "prompt", AnalysisReport
            )
    finally:
        db.close()

    assert len(calls) == 3


async def test_single_503_costs_exactly_one_rpd(monkeypatch):
    """503 計入 RPD，所以「送幾次」等於「扣幾次」——用量紀錄要能佐證。"""
    head = router.PREMIUM_CHAIN[0]
    calls: list[str] = []
    _no_delays(monkeypatch)
    monkeypatch.setattr(
        gemini.httpx,
        "AsyncClient",
        _client_factory(lambda m: 503 if m == head else 200, calls),
    )

    db = SessionLocal()
    try:
        await router.generate_premium_structured(db, "prompt", Tiny)
        from app.core.rate_limiter import used_today

        assert used_today(db, head) == 1
    finally:
        db.close()
