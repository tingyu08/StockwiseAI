from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import NotFoundError
from app.models import Stock
from app.services import premium_service

router = APIRouter(tags=["premium"])


@router.get("/premium", response_model=Envelope)
async def list_premium(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    return ok(premium_service.premium_list(db, market))


@router.post("/premium:refresh", response_model=Envelope)
async def refresh_premium(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    """立即抓當日淨值快照（也由每日排程自動執行）。"""
    return ok(await premium_service.snapshot_premiums(db, market))


@router.get("/premium/{symbol}/history", response_model=Envelope)
async def premium_history(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"尚未追蹤 {market}/{symbol}")
    return ok(premium_service.premium_history(db, stock))
