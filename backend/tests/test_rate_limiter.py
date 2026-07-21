from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import delete

from app.core import rate_limiter
from app.core.db import SessionLocal
from app.core.exceptions import QuotaExceededError
from app.models.analysis import AiQuotaReservation, AiUsageLog


MODEL = "gemini-3.1-flash-lite"


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
