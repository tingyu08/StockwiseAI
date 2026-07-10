from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError
from app.models import AiReport, DailyPrice, EtfNav, JobRun, Stock

router = APIRouter(tags=["health"])


class ReadinessError(AppError):
    status_code = 503


@router.get("/health", response_model=Envelope)
def health() -> Envelope:
    return ok({"status": "ok"})


@router.get("/health/live", response_model=Envelope)
def liveness() -> Envelope:
    return ok({"status": "alive"})


@router.get("/health/ready", response_model=Envelope)
def readiness(db: Session = Depends(get_db)) -> Envelope:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ReadinessError("資料庫目前無法使用") from exc
    return ok({"status": "ready", "database": "ok"})


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
        ai_rows = db.execute(
            select(AiReport.kind, func.max(AiReport.trade_date))
            .join(Stock, AiReport.stock_id == Stock.id)
            .where(Stock.market == market)
            .group_by(AiReport.kind)
        ).all()
        ai_dates = {kind: trade_date for kind, trade_date in ai_rows}
        latest_ai = max(ai_dates.values(), default=None)
        latest_job = db.execute(
            select(JobRun)
            .where(
                JobRun.status == "succeeded",
                JobRun.name.ilike(f"%-{market.lower()}"),
            )
            .order_by(JobRun.finished_at.desc(), JobRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        result[market] = {
            "latest_price_date": latest_price.isoformat() if latest_price else None,
            "latest_nav_date": latest_nav.isoformat() if latest_nav else None,
            "latest_ai_date": latest_ai.isoformat() if latest_ai else None,
            "latest_ai_dates": {
                kind: ai_dates[kind].isoformat() if kind in ai_dates else None
                for kind in ("news", "routine", "trade")
            },
            "latest_successful_job": {
                "id": latest_job.id,
                "name": latest_job.name,
                "finished_at": latest_job.finished_at.isoformat()
                if latest_job.finished_at
                else None,
            }
            if latest_job
            else None,
        }
    return ok(result)
