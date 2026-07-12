from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Indicator, Stock
from app.services.market_gateway import market_data
from app.services.sync_service import ensure_stock, sync_prices
from app.services.time_service import market_today

router = APIRouter(tags=["stocks"])

RANGE_DAYS = {"3m": 90, "6m": 180, "1y": 365}


class AddStockBody(BaseModel):
    market: Literal["TW", "US"]
    symbol: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")


@router.get("/stocks", response_model=Envelope)
async def search_stocks(
    market: Literal["TW", "US"] = Query(...),
    q: str = Query(min_length=1, max_length=32),
    db: Session = Depends(get_db),
) -> Envelope:
    """先查本地 DB，無結果再問 provider（美股以代號直接驗證）。"""
    pattern = f"%{q}%"
    local = db.execute(
        select(Stock)
        .where(Stock.market == market)
        .where((Stock.symbol.ilike(pattern)) | (Stock.name.ilike(pattern)))
        .limit(20)
    ).scalars().all()
    if local:
        return ok([_stock_dto(s) for s in local])

    remote = await market_data.search_stocks(market, q.strip())
    return ok([
        {"symbol": r.symbol, "market": market, "name": r.name,
         "currency": r.currency, "kind": r.kind, "tracked": False}
        for r in remote
    ])


@router.post("/stocks", response_model=Envelope)
async def add_stock(body: AddStockBody, db: Session = Depends(get_db)) -> Envelope:
    """建檔並首次同步（首次約 400 天日線）。"""
    stock = await ensure_stock(db, body.market, body.symbol.upper() if body.market == "US" else body.symbol)
    added = await sync_prices(db, stock)
    return ok({**_stock_dto(stock), "synced_rows": added})


@router.get("/stocks/{symbol}/prices", response_model=Envelope)
async def get_prices(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    range_: Literal["3m", "6m", "1y"] = Query("1y", alias="range"),
    db: Session = Depends(get_db),
) -> Envelope:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"尚未追蹤 {market}/{symbol}，請先透過搜尋加入")

    since = market_today(market) - timedelta(days=RANGE_DAYS[range_])
    prices = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id == stock.id, DailyPrice.date >= since)
        .order_by(DailyPrice.date)
    ).scalars().all()
    indicators = db.execute(
        select(Indicator)
        .where(Indicator.stock_id == stock.id, Indicator.date >= since)
        .order_by(Indicator.date)
    ).scalars().all()
    ind_by_date = {i.date: i for i in indicators}

    series = []
    for p in prices:
        i = ind_by_date.get(p.date)
        series.append(
            {
                "date": p.date.isoformat(),
                "open": _num(p.open), "high": _num(p.high),
                "low": _num(p.low), "close": _num(p.close), "volume": p.volume,
                "ma5": _num(i.ma5) if i else None,
                "ma20": _num(i.ma20) if i else None,
                "ma60": _num(i.ma60) if i else None,
                "rsi14": _num(i.rsi14) if i else None,
                "kd_k": _num(i.kd_k) if i else None,
                "kd_d": _num(i.kd_d) if i else None,
                "macd": _num(i.macd) if i else None,
                "macd_signal": _num(i.macd_signal) if i else None,
                "bb_upper": _num(i.bb_upper) if i else None,
                "bb_lower": _num(i.bb_lower) if i else None,
            }
        )
    return ok({"stock": _stock_dto(stock), "series": series})


def _stock_dto(s: Stock) -> dict:
    return {
        "symbol": s.symbol, "market": s.market, "name": s.name,
        "currency": s.currency, "kind": s.kind, "tracked": True,
    }


def _num(v) -> float | None:
    return float(v) if v is not None else None
