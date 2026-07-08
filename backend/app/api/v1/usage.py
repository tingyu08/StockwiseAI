from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.rate_limiter import used_today

router = APIRouter(tags=["usage"])


@router.get("/usage", response_model=Envelope)
async def get_usage(db: Session = Depends(get_db)) -> Envelope:
    """各模型今日已用/剩餘請求數（前端額度儀表板）。"""
    quotas = get_settings().load_quotas()
    data = [
        {
            "model": model,
            "rpd": quota.rpd,
            "used": (used := used_today(db, model)),
            "remaining": max(0, quota.rpd - used),
        }
        for model, quota in quotas.items()
    ]
    return ok(data)
