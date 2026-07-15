from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.models import Stock
from app.services.dashboard_service import build_dashboard
from app.services.market_gateway import market_data
from app.services.stock_read_service import get_price_series, get_stock, stock_dto
from app.services.sync_service import ensure_stock, sync_prices

router = APIRouter(tags=["stocks"])


class AddStockBody(BaseModel):
    market: Literal["TW", "US"]
    symbol: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")


@router.get("/stocks", response_model=Envelope)
async def search_stocks(
    market: Literal["TW", "US"] = Query(...),
    q: str = Query(min_length=1, max_length=32),
    db: Session = Depends(get_db),
) -> Envelope:
    pattern = f"%{q}%"
    local = db.execute(
        select(Stock)
        .where(Stock.market == market)
        .where((Stock.symbol.ilike(pattern)) | (Stock.name.ilike(pattern)))
        .limit(20)
    ).scalars().all()
    if local:
        return ok([stock_dto(stock) for stock in local])

    remote = await market_data.search_stocks(market, q.strip())
    return ok(
        [
            {
                "symbol": result.symbol,
                "market": market,
                "name": result.name,
                "currency": result.currency,
                "kind": result.kind,
                "tracked": False,
            }
            for result in remote
        ]
    )


@router.post("/stocks", response_model=Envelope)
async def add_stock(body: AddStockBody, db: Session = Depends(get_db)) -> Envelope:
    symbol = body.symbol.upper() if body.market == "US" else body.symbol
    stock = await ensure_stock(db, body.market, symbol)
    added = await sync_prices(stock.id, stock.market, stock.symbol)
    return ok({**stock_dto(stock), "synced_rows": added})


@router.get("/stocks/{symbol}/dashboard", response_model=Envelope)
def get_dashboard(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    range_: Literal["3m", "6m", "1y"] = Query("1y", alias="range"),
    db: Session = Depends(get_db),
) -> Envelope:
    return ok(build_dashboard(db, market, symbol, range_))


@router.get("/stocks/{symbol}/prices", response_model=Envelope)
async def get_prices(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    range_: Literal["3m", "6m", "1y"] = Query("1y", alias="range"),
    db: Session = Depends(get_db),
) -> Envelope:
    return ok(get_price_series(db, get_stock(db, market, symbol), range_))
