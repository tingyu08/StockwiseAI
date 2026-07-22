"""Retention policies for operational tables that otherwise grow without bound."""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import AiQuotaReservation, AiUsageLog, JobRun
from app.services.job_service import utc_now

# 預約正常會在 AI 呼叫結束時被 finalize/cancel 掉；殘留的都是行程被砍、
# 連線中斷等異常留下的孤兒。rate_limiter.used_today() 會把活著的預約
# 計入已用量，所以孤兒等於憑空吃掉當日額度（rpd=20 的模型特別有感），
# 且資料表本身無上限成長。取一個遠大於任何單次 AI 呼叫的門檻。
STALE_RESERVATION_MINUTES = 60


def cleanup_expired_records(
    db: Session,
    *,
    now: datetime | None = None,
    successful_job_days: int = 30,
    failed_job_days: int = 90,
    usage_days: int = 90,
    stale_reservation_minutes: int = STALE_RESERVATION_MINUTES,
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
    stale_reservations = db.execute(
        delete(AiQuotaReservation).where(
            AiQuotaReservation.created_at
            < now - timedelta(minutes=stale_reservation_minutes)
        )
    ).rowcount
    db.commit()
    return {
        "successful_jobs_deleted": successful or 0,
        "failed_jobs_deleted": failed or 0,
        "usage_logs_deleted": usage or 0,
        "stale_reservations_deleted": stale_reservations or 0,
    }
