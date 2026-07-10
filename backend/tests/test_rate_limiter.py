from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import delete

from app.core import rate_limiter
from app.core.db import SessionLocal
from app.core.exceptions import QuotaExceededError
from app.models.analysis import AiUsageLog


MODEL = "gemini-3.1-flash-lite"


@pytest.fixture
def db():
    session = SessionLocal()
    session.execute(delete(AiUsageLog).where(AiUsageLog.model == MODEL))
    session.commit()
    yield session
    session.execute(delete(AiUsageLog).where(AiUsageLog.model == MODEL))
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


def test_daily_quota_uses_taipei_calendar_day():
    now = datetime(2026, 7, 10, 16, 30, tzinfo=timezone.utc)
    bounds = getattr(rate_limiter, "taipei_day_bounds_utc", lambda _now: (None, None))

    start, end = bounds(now)

    assert start == datetime(2026, 7, 10, 16, 0)
    assert end == datetime(2026, 7, 11, 16, 0)
