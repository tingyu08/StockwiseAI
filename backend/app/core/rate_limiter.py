"""Database-backed per-model RPD, RPM, and TPM quota checks."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import QuotaExceededError
from app.models.analysis import AiQuotaReservation, AiUsageLog

GOOGLE_QUOTA_TZ = ZoneInfo("America/Los_Angeles")
_LOCAL_LOCKS: defaultdict[str, Lock] = defaultdict(Lock)


def provider_day_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return Google's current Pacific quota-day bounds as naive UTC values."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_day = now.astimezone(GOOGLE_QUOTA_TZ).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=GOOGLE_QUOTA_TZ)
    end_local = datetime.combine(
        local_day + timedelta(days=1), time.min, tzinfo=GOOGLE_QUOTA_TZ
    )
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def used_today(db: Session, model: str, now: datetime | None = None) -> int:
    start, end = provider_day_bounds_utc(now)
    stmt = (
        select(func.count())
        .select_from(AiUsageLog)
        .where(AiUsageLog.model == model)
        .where(AiUsageLog.created_at >= start)
        .where(AiUsageLog.created_at < end)
    )
    completed = db.execute(stmt).scalar_one()
    active = db.execute(
        select(func.count())
        .select_from(AiQuotaReservation)
        .where(
            AiQuotaReservation.model == model,
            AiQuotaReservation.created_at >= start,
            AiQuotaReservation.created_at < end,
        )
    ).scalar_one()
    return completed + active


def usage_snapshot(db: Session) -> list[dict]:
    quotas = get_settings().load_quotas()
    return [
        {
            "model": model,
            "rpd": quota.rpd,
            "used": (used := used_today(db, model)),
            "remaining": max(0, quota.rpd - used),
        }
        for model, quota in quotas.items()
    ]


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
    rpm_used += db.execute(
        select(func.count())
        .select_from(AiQuotaReservation)
        .where(
            AiQuotaReservation.model == model,
            AiQuotaReservation.created_at >= minute_start,
            AiQuotaReservation.created_at <= now,
        )
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
    reserved_tokens = db.execute(
        select(func.coalesce(func.sum(AiQuotaReservation.estimated_tokens), 0)).where(
            AiQuotaReservation.model == model,
            AiQuotaReservation.created_at >= minute_start,
            AiQuotaReservation.created_at <= now,
        )
    ).scalar_one()
    if int(tokens_used) + int(reserved_tokens) + estimated_tokens > quota.tpm:
        raise QuotaExceededError(f"{model} TPM 額度已用盡")


def reserve_quota(db: Session, model: str, estimated_tokens: int = 0) -> int:
    """Atomically reserve one provider request before sending it."""
    with _LOCAL_LOCKS[model]:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtext(f"stockwise-ai-quota:{model}")
                    )
                )
            )
        ensure_quota(db, model, estimated_tokens=estimated_tokens)
        reservation = AiQuotaReservation(
            model=model, estimated_tokens=max(0, estimated_tokens)
        )
        db.add(reservation)
        db.commit()
        # 不可在此 refresh／再讀任何資料：這裡回傳後緊接著就是漫長的 AI HTTP 呼叫，
        # 任何一句 SQL 都會開啟新交易並在呼叫期間閒置，觸發 Neon 的
        # idle_in_transaction_session_timeout 砍掉連線（pool_pre_ping 只在取出
        # 連線時檢查，救不了握在手上的連線）。expire_on_commit=False，
        # commit 後 id 仍在（INSERT 由 RETURNING 取得），毋須 refresh。
        return reservation.id


def finalize_quota(
    db: Session,
    reservation_id: int,
    *,
    provider: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Convert an in-flight reservation into an immutable usage record."""
    reservation = db.get(AiQuotaReservation, reservation_id)
    if reservation is None:
        return
    db.add(
        AiUsageLog(
            provider=provider,
            model=reservation.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=reservation.created_at,
        )
    )
    db.delete(reservation)
    db.commit()


def cancel_quota(db: Session, reservation_id: int) -> None:
    """Release a reservation only when the request was never sent."""
    reservation = db.get(AiQuotaReservation, reservation_id)
    if reservation is not None:
        db.delete(reservation)
        db.commit()
