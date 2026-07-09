from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError, NotFoundError
from app.models import Stock, WatchGroup, WatchlistItem
from app.services.sync_service import ensure_stock, sync_prices

router = APIRouter(tags=["watchlist"])


class ConflictError(AppError):
    status_code = 409


class AddWatchBody(BaseModel):
    market: Literal["TW", "US"]
    symbol: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")


class PatchWatchBody(BaseModel):
    ai_managed: bool | None = None
    group_id: int | None = None
    clear_group: bool = False


class ReorderItem(BaseModel):
    symbol: str
    group_id: int | None = None
    sort_order: int


class ReorderBody(BaseModel):
    market: Literal["TW", "US"]
    items: list[ReorderItem]


class GroupBody(BaseModel):
    market: Literal["TW", "US"]
    name: str = Field(min_length=1, max_length=32)


# ---- 群組 ----

@router.get("/groups", response_model=Envelope)
async def list_groups(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    groups = db.execute(
        select(WatchGroup).where(WatchGroup.market == market).order_by(WatchGroup.sort_order, WatchGroup.id)
    ).scalars().all()
    return ok([{"id": g.id, "name": g.name} for g in groups])


@router.post("/groups", response_model=Envelope)
async def create_group(body: GroupBody, db: Session = Depends(get_db)) -> Envelope:
    exists = db.execute(
        select(WatchGroup).where(WatchGroup.market == body.market, WatchGroup.name == body.name)
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"群組「{body.name}」已存在")
    group = WatchGroup(market=body.market, name=body.name)
    db.add(group)
    db.commit()
    return ok({"id": group.id, "name": group.name})


@router.delete("/groups/{group_id}", response_model=Envelope)
async def delete_group(group_id: int, db: Session = Depends(get_db)) -> Envelope:
    group = db.get(WatchGroup, group_id)
    if group is None:
        raise NotFoundError("查無此群組")
    # 群組內股票移回「未分組」，不刪除股票
    for item in db.execute(
        select(WatchlistItem).where(WatchlistItem.group_id == group_id)
    ).scalars().all():
        item.group_id = None
    db.delete(group)
    db.commit()
    return ok({"deleted": group_id})


# ---- 自選清單 ----

@router.get("/watchlist", response_model=Envelope)
async def list_watchlist(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    rows = db.execute(
        select(WatchlistItem, Stock)
        .join(Stock, WatchlistItem.stock_id == Stock.id)
        .where(Stock.market == market)
        .order_by(WatchlistItem.sort_order, WatchlistItem.created_at)
    ).all()
    return ok([
        {
            "symbol": s.symbol, "name": s.name, "market": s.market, "kind": s.kind,
            "ai_managed": w.ai_managed, "group_id": w.group_id, "sort_order": w.sort_order,
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
        max_order = db.execute(
            select(WatchlistItem.sort_order)
            .join(Stock, WatchlistItem.stock_id == Stock.id)
            .where(Stock.market == body.market)
            .order_by(WatchlistItem.sort_order.desc())
            .limit(1)
        ).scalar_one_or_none()
        db.add(WatchlistItem(stock_id=stock.id, sort_order=(max_order or 0) + 1))
        db.commit()
    await sync_prices(db, stock)
    return ok({"symbol": stock.symbol, "market": stock.market, "name": stock.name})


@router.patch("/watchlist/{symbol}", response_model=Envelope)
async def patch_watch(
    symbol: str,
    body: PatchWatchBody,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    """更新 AI 託管或所屬群組。"""
    item = _get_item(db, market, symbol)
    if body.ai_managed is not None:
        item.ai_managed = body.ai_managed
    if body.clear_group:
        item.group_id = None
    elif body.group_id is not None:
        if db.get(WatchGroup, body.group_id) is None:
            raise NotFoundError("查無此群組")
        item.group_id = body.group_id
    db.commit()
    return ok({"symbol": symbol, "ai_managed": item.ai_managed, "group_id": item.group_id})


@router.put("/watchlist/reorder", response_model=Envelope)
async def reorder_watchlist(body: ReorderBody, db: Session = Depends(get_db)) -> Envelope:
    """整批更新排序與群組歸屬（前端上/下移或搬移群組後送出全清單）。"""
    for entry in body.items:
        item = _get_item(db, body.market, entry.symbol)
        item.sort_order = entry.sort_order
        item.group_id = entry.group_id
    db.commit()
    return ok({"updated": len(body.items)})


@router.delete("/watchlist/{symbol}", response_model=Envelope)
async def remove_watch(
    symbol: str, market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    item = _get_item(db, market, symbol)
    db.delete(item)
    db.commit()
    return ok({"removed": symbol})


def _get_item(db: Session, market: str, symbol: str) -> WatchlistItem:
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
    return item
