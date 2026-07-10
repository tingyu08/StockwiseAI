from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AiUsageLog, JobRun
from app.services.maintenance_service import cleanup_expired_records


def test_cleanup_uses_longer_retention_for_failed_jobs(client):
    now = datetime(2026, 7, 10, 12, 0)
    db = SessionLocal()
    try:
        db.add_all(
            [
                JobRun(
                    name="retention-old-success",
                    status="succeeded",
                    finished_at=now - timedelta(days=31),
                ),
                JobRun(
                    name="retention-old-failure",
                    status="failed",
                    finished_at=now - timedelta(days=91),
                ),
                JobRun(
                    name="retention-kept-failure",
                    status="failed",
                    finished_at=now - timedelta(days=60),
                ),
                JobRun(
                    name="retention-kept-queued",
                    status="queued",
                    created_at=now - timedelta(days=120),
                ),
                AiUsageLog(
                    provider="test",
                    model="retention-old-usage",
                    created_at=now - timedelta(days=91),
                ),
            ]
        )
        db.commit()

        result = cleanup_expired_records(db, now=now)

        names = set(
            db.execute(
                select(JobRun.name).where(JobRun.name.like("retention-%"))
            ).scalars()
        )
        assert result["successful_jobs_deleted"] >= 1
        assert result["failed_jobs_deleted"] >= 1
        assert result["usage_logs_deleted"] >= 1
        assert names == {"retention-kept-failure", "retention-kept-queued"}
        assert db.execute(
            select(AiUsageLog).where(AiUsageLog.model == "retention-old-usage")
        ).scalar_one_or_none() is None
    finally:
        db.close()
