from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import NotFoundError
from app.models import Stock, WatchlistItem
from app.services.sync_service import ensure_stock, sync_prices

router = APIRouter(tags=["watchlist"])


class AddWatchBody(BaseModel):
    market: Literal["TW", "US"]
    symbol: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")


@router.get("/watchlist", response_model=Envelope)
async def list_watchlist(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    rows = db.execute(
        select(WatchlistItem, Stock)
        .join(Stock, WatchlistItem.stock_id == Stock.id)
        .where(Stock.market == market)
        .order_by(WatchlistItem.created_at)
    ).all()
    return ok([
        {
            "symbol": s.symbol, "name": s.name, "market": s.market,
            "kind": s.kind, "ai_managed": w.ai_managed,
        }
        for w, s in rows
    ])


@router.post("/watchlist", response_model=Envelope)
async def add_watch(body: AddWatchBody, db: Session = Depends(get_db)) -> Envelope:
    symbol = body.symbol.upper() if body.market == "US" else body.symbol
    stock = await ensure_stock(db, body.market, symbol)
    existing = db.execute(
        select(WatchlistItem).where(WatchlistItem.stock_id == stock.id)
    ).scalar_one_or_none()
    if existing is None:
        db.add(WatchlistItem(stock_id=stock.id))
        db.commit()
    await sync_prices(db, stock)
    return ok({"symbol": stock.symbol, "market": stock.market, "name": stock.name})


class PatchWatchBody(BaseModel):
    ai_managed: bool


@router.patch("/watchlist/{symbol}", response_model=Envelope)
async def patch_watch(
    symbol: str,
    body: PatchWatchBody,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    """切換是否交給 AI 模擬操作。"""
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"查無 {market}/{symbol}")
    item = db.execute(
        select(WatchlistItem).where(WatchlistItem.stock_id == stock.id)
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError(f"{symbol} 不在自選清單中")
    item.ai_managed = body.ai_managed
    db.commit()
    return ok({"symbol": symbol, "ai_managed": item.ai_managed})


@router.delete("/watchlist/{symbol}", response_model=Envelope)
async def remove_watch(
    symbol: str, market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"查無 {market}/{symbol}")
    item = db.execute(
        select(WatchlistItem).where(WatchlistItem.stock_id == stock.id)
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError(f"{symbol} 不在自選清單中")
    db.delete(item)
    db.commit()
    return ok({"removed": symbol})
