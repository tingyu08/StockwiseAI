"""暫時性上游故障（503）的退避行為。

2026-08-05 20:11 的實例：gemini-3.5-flash-lite 連三次回 503，provider 層
退避只有 1s/2s，job 層失敗後又立刻 requeue（零延遲），於是三次 job 嘗試
在 37 秒內全部燒在同一段 Google 故障上，整批美股分析當日無產出。

兩層退避各自負責不同的時間尺度：
  provider 層（秒級）  503 是上游過載，秒級重打幾乎必然再吃一次
  job 層（分鐘級）     同一批工作的下一次嘗試要跨過故障窗口
"""
from datetime import timedelta

from app.core.db import SessionLocal
from app.models import JobRun
from app.providers.ai import gemini
from app.providers.ai.schemas import AnalysisReport
from app.providers.ai.gemini import GeminiProvider
from app.services import job_service


# ---- provider 層：503 專屬退避 ----


def test_service_unavailable_delay_is_far_longer_than_generic_backoff(monkeypatch):
    """503 不能沿用 429 的 1s/2s——那是為「Google 會給 retryDelay」設計的。"""
    delay = getattr(gemini, "_service_unavailable_delay", None)
    assert callable(delay), "缺少 503 專屬退避函式"
    monkeypatch.setattr(gemini.random, "uniform", lambda low, high: 0.25)

    assert delay(0) == 5.25
    assert delay(1) == 15.25
    assert delay(2) == 45.25
    # 每一級都必須明顯長於通用退避，否則等於沒改
    for retry in range(3):
        assert delay(retry) > gemini._retry_delay(retry) * 3


async def test_gemini_503_retry_uses_service_unavailable_delay(monkeypatch):
    """503 路徑必須走長退避；沿用 _retry_delay 就是這次故障的原樣重演。"""
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
    # 通用退避回 0：若 503 誤用它，sleeps 會是 [0] 而非長退避
    monkeypatch.setattr(gemini, "_retry_delay", lambda _retry: 0, raising=False)
    monkeypatch.setattr(
        gemini, "_service_unavailable_delay", lambda retry: 100 + retry, raising=False
    )

    db = SessionLocal()
    try:
        result = await GeminiProvider("gemini-3.6-flash", db)._call_api(
            "prompt", AnalysisReport
        )
    finally:
        db.close()

    assert result == "{}"
    assert sleeps == [100], f"503 沒有走長退避：{sleeps}"


# ---- job 層：重試前的分鐘級延遲 ----


def _cleanup(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is not None:
            db.delete(run)
            db.commit()
    finally:
        db.close()


def test_job_retry_delay_grows_with_attempts():
    delay = getattr(job_service, "_job_retry_delay", None)
    assert callable(delay), "缺少 job 層重試退避函式"

    assert delay(1) == timedelta(minutes=1)
    assert delay(2) == timedelta(minutes=5)
    # 之後不再拉長（max_attempts 預設 3，第 3 次已是最後一次）
    assert delay(3) == timedelta(minutes=5)


def _force_running(run_id: int) -> None:
    """把工作直接推進 running，不經 claim_next_job。

    佇列是共享的：全套執行時別的測試可能留有 queued 工作，
    claim_next_job() 未必領到我們這一筆。
    """
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        run.status = "running"
        run.attempts = 1
        run.started_at = job_service.utc_now()
        db.commit()
    finally:
        db.close()


async def test_failed_job_waits_before_next_attempt():
    """失敗後不得立刻可領——那會讓三次嘗試在幾十秒內燒完。"""
    run_id = job_service.enqueue_job(
        "backoff-test", job_type="test", payload={}, max_attempts=3
    )
    _force_running(run_id)

    async def fails(job_type, payload):
        raise RuntimeError("gemini-3.5-flash-lite returned 503 after 3 attempts")

    await job_service.execute_claimed_job(run_id, dispatcher=fails)

    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        assert run.status == "queued"
        assert run.next_attempt_at is not None, "重試沒有排定延遲"
        assert run.next_attempt_at > job_service.utc_now()
    finally:
        db.close()
    _cleanup(run_id)


async def test_claim_skips_job_still_inside_backoff_window():
    """退避未到期的工作留在佇列裡，但不可被領取。

    只斷言「不會領到這一筆」而非「領不到任何工作」：佇列共享，
    全套執行時別的測試可能留有 queued 工作。
    """
    run_id = job_service.enqueue_job(
        "backoff-skip-test", job_type="test", payload={}, max_attempts=3
    )

    def set_backoff(delta: timedelta) -> None:
        db = SessionLocal()
        try:
            run = db.get(JobRun, run_id)
            run.next_attempt_at = job_service.utc_now() + delta
            db.commit()
        finally:
            db.close()

    set_backoff(timedelta(minutes=1))
    claimed = []
    while (got := job_service.claim_next_job()) is not None:
        claimed.append(got)
    assert run_id not in claimed, "退避未到期就被領走了"

    # 退避到期後恢復可領
    set_backoff(timedelta(seconds=-1))
    assert job_service.claim_next_job() == run_id
    _cleanup(run_id)


def test_manual_retry_clears_backoff():
    """人工按下重試是明確意圖，不該再等退避。"""
    run_id = job_service.enqueue_job(
        "backoff-manual-test", job_type="test", payload={}, max_attempts=1
    )
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        run.status = "failed"
        run.attempts = 1
        run.next_attempt_at = job_service.utc_now() + timedelta(minutes=5)
        db.commit()
    finally:
        db.close()

    job_service.retry_job(run_id)

    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        assert run.status == "queued"
        assert run.next_attempt_at is None
    finally:
        db.close()
    _cleanup(run_id)
