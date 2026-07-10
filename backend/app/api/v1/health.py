from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.models import AiReport, DailyPrice, EtfNav, Stock

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Envelope)
def health() -> Envelope:
    return ok({"status": "ok"})


@router.get("/data-status", response_model=Envelope)
def data_status(db: Session = Depends(get_db)) -> Envelope:
    result = {}
    for market in ("TW", "US"):
        latest_price = db.execute(
            select(func.max(DailyPrice.date))
            .join(Stock, DailyPrice.stock_id == Stock.id)
            .where(Stock.market == market)
        ).scalar_one_or_none()
        latest_nav = db.execute(
            select(func.max(EtfNav.date))
            .join(Stock, EtfNav.stock_id == Stock.id)
            .where(Stock.market == market)
        ).scalar_one_or_none()
        latest_ai = db.execute(
            select(func.max(AiReport.trade_date))
            .join(Stock, AiReport.stock_id == Stock.id)
            .where(Stock.market == market)
        ).scalar_one_or_none()
        result[market] = {
            "latest_price_date": latest_price.isoformat() if latest_price else None,
            "latest_nav_date": latest_nav.isoformat() if latest_nav else None,
            "latest_ai_date": latest_ai.isoformat() if latest_ai else None,
        }
    return ok(result)
