"""Database-backed per-model RPD, RPM, and TPM quota checks."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import QuotaExceededError
from app.models.analysis import AiUsageLog

TAIPEI = ZoneInfo("Asia/Taipei")


def taipei_day_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the current Taipei calendar-day bounds as naive UTC values."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_day = now.astimezone(TAIPEI).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=TAIPEI)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def used_today(db: Session, model: str, now: datetime | None = None) -> int:
    start, end = taipei_day_bounds_utc(now)
    stmt = (
        select(func.count())
        .select_from(AiUsageLog)
        .where(AiUsageLog.model == model)
        .where(AiUsageLog.created_at >= start)
        .where(AiUsageLog.created_at < end)
    )
    return db.execute(stmt).scalar_one()


def remaining_today(db: Session, model: str) -> int:
    quotas = get_settings().load_quotas()
    if model not in quotas:
        raise QuotaExceededError(f"未設定 {model} 的額度，請檢查 quotas.yaml")
    return max(0, quotas[model].rpd - used_today(db, model))


def ensure_quota(
    db: Session,
    model: str,
    needed: int = 1,
    estimated_tokens: int = 0,
) -> None:
    """Reject calls that would exceed the model's daily or rolling-minute quota."""
    quotas = get_settings().load_quotas()
    if model not in quotas:
        raise QuotaExceededError(f"未設定 {model} 的額度，請檢查 quotas.yaml")
    quota = quotas[model]
    if remaining_today(db, model) < needed:
        raise QuotaExceededError(f"{model} 今日免費額度已用盡")

    now = _utc_now_naive()
    minute_start = now - timedelta(minutes=1)
    window = (
        AiUsageLog.model == model,
        AiUsageLog.created_at >= minute_start,
        AiUsageLog.created_at <= now,
    )
    rpm_used = db.execute(
        select(func.count()).select_from(AiUsageLog).where(*window)
    ).scalar_one()
    if rpm_used + needed > quota.rpm:
        raise QuotaExceededError(f"{model} RPM 額度已用盡")

    tokens_used = db.execute(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(AiUsageLog.input_tokens, 0)
                    + func.coalesce(AiUsageLog.output_tokens, 0)
                ),
                0,
            )
        ).where(*window)
    ).scalar_one()
    if int(tokens_used) + estimated_tokens > quota.tpm:
        raise QuotaExceededError(f"{model} TPM 額度已用盡")
