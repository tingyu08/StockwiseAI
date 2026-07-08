from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import NotFoundError
from app.models import Stock
from app.services.prediction_service import get_predictions

router = APIRouter(tags=["predictions"])


@router.get("/stocks/{symbol}/predictions", response_model=Envelope)
async def predictions(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"尚未追蹤 {market}/{symbol}")
    return ok(get_predictions(db, stock))
