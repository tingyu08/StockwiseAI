from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import delete

from app.core import rate_limiter
from app.core.db import SessionLocal
from app.core.exceptions import QuotaExceededError
from app.models.analysis import AiQuotaReservation, AiUsageLog


MODEL = "gemini-3.5-flash-lite"


@pytest.fixture
def db():
    session = SessionLocal()
    session.execute(delete(AiUsageLog).where(AiUsageLog.model == MODEL))
    session.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model == MODEL))
    session.commit()
    yield session
    session.execute(delete(AiUsageLog).where(AiUsageLog.model == MODEL))
    session.execute(delete(AiQuotaReservation).where(AiQuotaReservation.model == MODEL))
    session.commit()
    session.close()


def test_quota_enforces_requests_per_minute(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all([
        AiUsageLog(provider="test", model=MODEL, created_at=now - timedelta(seconds=i))
        for i in range(15)
    ])
    db.commit()

    with pytest.raises(QuotaExceededError, match="RPM"):
        rate_limiter.ensure_quota(db, MODEL)


def test_quota_enforces_tokens_per_minute(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(AiUsageLog(
        provider="test", model=MODEL, input_tokens=249_900,
        output_tokens=100, created_at=now,
    ))
    db.commit()

    if "estimated_tokens" not in inspect.signature(rate_limiter.ensure_quota).parameters:
        pytest.fail("ensure_quota must accept an estimated_tokens reservation")
    with pytest.raises(QuotaExceededError, match="TPM"):
        rate_limiter.ensure_quota(db, MODEL, estimated_tokens=1)


def test_daily_quota_uses_google_pacific_calendar_day_in_summer():
    now = datetime(2026, 7, 10, 16, 30, tzinfo=timezone.utc)
    bounds = getattr(rate_limiter, "provider_day_bounds_utc", lambda _now: (None, None))

    start, end = bounds(now)

    assert start == datetime(2026, 7, 10, 7, 0)
    assert end == datetime(2026, 7, 11, 7, 0)


def test_daily_quota_uses_google_pacific_calendar_day_in_winter():
    now = datetime(2026, 1, 10, 16, 30, tzinfo=timezone.utc)
    bounds = getattr(rate_limiter, "provider_day_bounds_utc", lambda _now: (None, None))

    start, end = bounds(now)

    assert start == datetime(2026, 1, 10, 8, 0)
    assert end == datetime(2026, 1, 11, 8, 0)


def test_active_reservation_consumes_the_last_rpm_slot(db):
    reserve = getattr(rate_limiter, "reserve_quota", None)
    assert callable(reserve)
    if not reserve:
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all(
        [
            AiUsageLog(provider="test", model=MODEL, created_at=now - timedelta(seconds=i))
            for i in range(14)
        ]
    )
    db.commit()

    reservation_id = reserve(db, MODEL, estimated_tokens=100)
    assert isinstance(reservation_id, int)
    with pytest.raises(QuotaExceededError, match="RPM"):
        reserve(db, MODEL, estimated_tokens=100)


def test_reserve_leaves_no_open_transaction(db):
    """預約後緊接著是漫長的 AI HTTP 呼叫：此時若還開著交易，Neon 會以
    idle_in_transaction_session_timeout 砍掉連線，導致呼叫回來後 finalize 失敗。"""
    reservation_id = rate_limiter.reserve_quota(db, MODEL, estimated_tokens=100)

    assert isinstance(reservation_id, int)  # 不 refresh 也必須拿得到 id
    assert not db.in_transaction(), "reserve_quota 回傳後不得留下開啟中的交易"


# ---- 預約生命週期：洩漏會憑空吃掉當日額度 ----

async def test_cancelled_call_releases_reservation(monkeypatch):
    """asyncio.CancelledError 繼承 BaseException，既有 except 全攔不到。

    沒有 try/finally 兜底時，關機或外層 timeout 會讓預約永遠留在
    ai_quota_reservations，而 used_today() 把活著的預約計入已用量。
    """
    import asyncio

    import httpx

    from app.core.db import SessionLocal
    from app.models.analysis import AiQuotaReservation
    from app.providers.ai.gemini import GeminiProvider
    from app.providers.ai.schemas import BatchAnalysisResult

    db = SessionLocal()
    try:
        before = db.query(AiQuotaReservation).count()

        class _Cancelling:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                raise asyncio.CancelledError()

        monkeypatch.setattr(httpx, "AsyncClient", _Cancelling)
        provider = GeminiProvider("gemini-3.5-flash-lite", db)

        with pytest.raises(asyncio.CancelledError):
            await provider._call_api("prompt", BatchAnalysisResult)

        assert db.query(AiQuotaReservation).count() == before, "取消後預約沒被釋放"
    finally:
        db.close()


async def test_connect_error_does_not_burn_quota(monkeypatch):
    """ConnectError＝請求從未抵達 Google，不該記成一次用量。"""
    import httpx

    from app.core.db import SessionLocal
    from app.core.exceptions import UpstreamError
    from app.models.analysis import AiUsageLog
    from app.providers.ai.gemini import GeminiProvider
    from app.providers.ai.schemas import BatchAnalysisResult

    db = SessionLocal()
    try:
        before = db.query(AiUsageLog).filter(
            AiUsageLog.model == "gemini-3.5-flash-lite"
        ).count()

        class _Refused:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "AsyncClient", _Refused)
        provider = GeminiProvider("gemini-3.5-flash-lite", db)

        with pytest.raises(UpstreamError):
            await provider._call_api("prompt", BatchAnalysisResult)

        after = db.query(AiUsageLog).filter(
            AiUsageLog.model == "gemini-3.5-flash-lite"
        ).count()
        assert after == before, "連線失敗被記成實際用量，白燒 RPD"
    finally:
        db.close()


def test_maintenance_sweeps_stale_reservations():
    """孤兒預約必須被回收，否則當日額度被永久佔用且資料表無上限成長。"""
    from datetime import timedelta

    from app.core.db import SessionLocal
    from app.models.analysis import AiQuotaReservation
    from app.services.job_service import utc_now
    from app.services.maintenance_service import cleanup_expired_records

    db = SessionLocal()
    try:
        stale = AiQuotaReservation(model="sweep-test", estimated_tokens=1)
        stale.created_at = utc_now() - timedelta(hours=3)
        fresh = AiQuotaReservation(model="sweep-test", estimated_tokens=1)
        fresh.created_at = utc_now()
        db.add_all([stale, fresh])
        db.commit()

        result = cleanup_expired_records(db)

        assert result["stale_reservations_deleted"] >= 1
        remaining = db.query(AiQuotaReservation).filter(
            AiQuotaReservation.model == "sweep-test"
        ).all()
        assert len(remaining) == 1, "進行中的預約不該被誤刪"
    finally:
        db.query(AiQuotaReservation).filter(
            AiQuotaReservation.model == "sweep-test"
        ).delete()
        db.commit()
        db.close()


def test_validate_configured_models_catches_typo(monkeypatch):
    """模型名稱打錯字必須在啟動時就炸，而不是偽裝成「額度用盡」。"""
    from app.providers.ai import router as ai_router

    ai_router.validate_configured_models()  # 目前設定應該是對的

    monkeypatch.setattr(ai_router, "ROUTINE_CHAIN", ["gemini-3.5-flash-litee"])
    with pytest.raises(ValueError, match="quotas.yaml 缺少模型設定"):
        ai_router.validate_configured_models()


# ---- 429 重試的額度帳（覆核抓到的迴歸）----

def _rate_limited_client(body: dict, calls: list):
    import httpx

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            calls.append(1)
            return httpx.Response(429, json=body)

    return _Client


async def test_429_retry_records_only_one_usage_row(monkeypatch):
    """重試不得把一次邏輯呼叫記成多次用量。

    每輪都 settle 的話，rpd 只有 20 的模型一次 429 就被記成用掉 3 次
    （15% 當日額度），額度帳反而比不重試更糟。
    """
    import httpx

    from app.core.db import SessionLocal
    from app.core.exceptions import QuotaExceededError
    from app.models.analysis import AiQuotaReservation, AiUsageLog
    from app.providers.ai import gemini as gemini_mod
    from app.providers.ai.gemini import GeminiProvider
    from app.providers.ai.schemas import BatchAnalysisResult

    model = "gemini-3.5-flash-lite"
    calls: list = []
    monkeypatch.setattr(httpx, "AsyncClient", _rate_limited_client({}, calls))
    monkeypatch.setattr(gemini_mod, "_sleep", lambda _s: asyncio_sleep_zero())

    db = SessionLocal()
    try:
        before_usage = db.query(AiUsageLog).filter(AiUsageLog.model == model).count()
        before_res = db.query(AiQuotaReservation).count()

        with pytest.raises(QuotaExceededError):
            await GeminiProvider(model, db)._call_api("prompt", BatchAnalysisResult)

        after_usage = db.query(AiUsageLog).filter(AiUsageLog.model == model).count()
        assert len(calls) > 1, "應該有重試"
        assert after_usage - before_usage == 1, (
            f"一次呼叫寫了 {after_usage - before_usage} 筆用量（重試被重複計費）"
        )
        assert db.query(AiQuotaReservation).count() == before_res, "預約沒被清乾淨"
    finally:
        db.close()


async def asyncio_sleep_zero():
    return None


async def test_429_with_long_retry_delay_gives_up_immediately(monkeypatch):
    """Google 要求的等待超過上限時必須放棄，不可退回更短的指數退避。"""
    import httpx

    from app.core.db import SessionLocal
    from app.core.exceptions import QuotaExceededError
    from app.providers.ai import gemini as gemini_mod
    from app.providers.ai.gemini import GeminiProvider
    from app.providers.ai.schemas import BatchAnalysisResult

    body = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                 "retryDelay": "51s"}
            ]
        }
    }
    calls: list = []
    monkeypatch.setattr(httpx, "AsyncClient", _rate_limited_client(body, calls))
    monkeypatch.setattr(gemini_mod, "_sleep", lambda _s: asyncio_sleep_zero())

    db = SessionLocal()
    try:
        with pytest.raises(QuotaExceededError):
            await GeminiProvider("gemini-3.5-flash-lite", db)._call_api(
                "prompt", BatchAnalysisResult
            )
        assert len(calls) == 1, (
            f"Google 要求等 51 秒，卻仍重打了 {len(calls)} 次"
        )
    finally:
        db.close()


def test_parse_retry_delay_handles_malformed_bodies():
    """非 dict 的合法 JSON 不可拋 AttributeError——會逃出 router 的降級鏈。"""
    import httpx

    from app.providers.ai.gemini import (
        MIN_RETRY_AFTER_SEC,
        TOO_LONG,
        _parse_retry_delay,
    )

    def parse(payload):
        return _parse_retry_delay(httpx.Response(429, json=payload))

    assert parse([{"error": {}}]) is None  # 頂層陣列
    assert parse(None) is None
    assert parse({"error": "Too Many Requests"}) is None  # error 是字串
    assert parse({"error": {"details": "nope"}}) is None
    assert _parse_retry_delay(httpx.Response(429, text="<html>")) is None

    info = "type.googleapis.com/google.rpc.RetryInfo"
    assert parse({"error": {"details": [{"@type": info, "retryDelay": "7s"}]}}) == 7.0
    assert parse({"error": {"details": [{"@type": info, "retryDelay": "51s"}]}}) == TOO_LONG
    assert parse({"error": {"details": [{"@type": info, "retryDelay": "-5s"}]}}) is None
    # "0s" 要有下限，否則變成零間隔連打
    assert parse(
        {"error": {"details": [{"@type": info, "retryDelay": "0s"}]}}
    ) == MIN_RETRY_AFTER_SEC
