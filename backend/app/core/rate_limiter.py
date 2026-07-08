"""按模型別的請求額度控管（RPD 為主，RPM 由呼叫端排隊自然滿足）。

額度數字來自 quotas.yaml；當日已用數來自 ai_usage_log 表，
重啟不會歸零、多進程也一致。
"""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import QuotaExceededError
from app.models.analysis import AiUsageLog


def used_today(db: Session, model: str) -> int:
    stmt = (
        select(func.count())
        .select_from(AiUsageLog)
        .where(AiUsageLog.model == model)
        .where(func.date(AiUsageLog.created_at) == date.today().isoformat())
    )
    return db.execute(stmt).scalar_one()


def remaining_today(db: Session, model: str) -> int:
    quotas = get_settings().load_quotas()
    if model not in quotas:
        raise QuotaExceededError(f"未設定 {model} 的額度，請檢查 quotas.yaml")
    return max(0, quotas[model].rpd - used_today(db, model))


def ensure_quota(db: Session, model: str, needed: int = 1) -> None:
    """呼叫 AI 前檢查；不足即丟 QuotaExceededError（由 Router 決定降級）。"""
    if remaining_today(db, model) < needed:
        raise QuotaExceededError(f"{model} 今日免費額度已用盡")
