"""Retention policies for operational tables that otherwise grow without bound."""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import AiUsageLog, JobRun
from app.services.job_service import utc_now


def cleanup_expired_records(
    db: Session,
    *,
    now: datetime | None = None,
    successful_job_days: int = 30,
    failed_job_days: int = 90,
    usage_days: int = 90,
) -> dict[str, int]:
    """Delete terminal operational history while retaining failures longer."""
    now = now or utc_now()
    successful = db.execute(
        delete(JobRun).where(
            JobRun.status == "succeeded",
            JobRun.finished_at < now - timedelta(days=successful_job_days),
        )
    ).rowcount
    failed = db.execute(
        delete(JobRun).where(
            JobRun.status == "failed",
            JobRun.finished_at < now - timedelta(days=failed_job_days),
        )
    ).rowcount
    usage = db.execute(
        delete(AiUsageLog).where(
            AiUsageLog.created_at < now - timedelta(days=usage_days)
        )
    ).rowcount
    db.commit()
    return {
        "successful_jobs_deleted": successful or 0,
        "failed_jobs_deleted": failed or 0,
        "usage_logs_deleted": usage or 0,
    }
