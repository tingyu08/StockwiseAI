from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError
from app.services.compare_service import compare

router = APIRouter(tags=["compare"])

MAX_SYMBOLS = 8


class BadRequestError(AppError):
    status_code = 400


@router.get("/compare", response_model=Envelope)
def compare_stocks(
    market: Literal["TW", "US"] = Query(...),
    symbols: str = Query(min_length=1, description="逗號分隔，如 2330,2317"),
    range_: Literal["3m", "6m", "1y"] = Query("1y", alias="range"),
    db: Session = Depends(get_db),
) -> Envelope:
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise BadRequestError("請至少提供一檔股票代號")
    if len(symbol_list) > MAX_SYMBOLS:
        raise BadRequestError(f"一次最多比較 {MAX_SYMBOLS} 檔")
    return ok(compare(db, market, symbol_list, range_))
