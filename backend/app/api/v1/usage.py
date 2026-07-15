from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.rate_limiter import usage_snapshot

router = APIRouter(tags=["usage"])


@router.get("/usage", response_model=Envelope)
def get_usage(db: Session = Depends(get_db)) -> Envelope:
    """各模型今日已用/剩餘請求數（前端額度儀表板）。"""
    return ok(usage_snapshot(db))
