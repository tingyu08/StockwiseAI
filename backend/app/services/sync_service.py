"""資料同步：外部源 → 本地 DB（增量抓價 → 重算指標 → upsert）。"""
import asyncio
import logging
import math
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine
from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Indicator, Stock
from app.providers.market.base import OhlcvRow
from app.services.indicator_service import compute_indicators
from app.services.market_gateway import market_data
from app.services.time_service import market_today

logger = logging.getLogger(__name__)

INITIAL_LOOKBACK_DAYS = 400  # 首次同步抓約 400 天（足夠算 MA60 且涵蓋一年走勢）
REFRESH_LOOKBACK_DAYS = 14  # 每次重抓近期窗口，接收來源修正並補中間缺口


def _clean(value: float | None) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


async def ensure_stock(db: Session, market: str, symbol: str) -> Stock:
    """DB 有就直接回傳；沒有則向 provider 驗證後建檔。"""
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock:
        return stock

    matches = await market_data.search_stocks(market, symbol)
    info = next((m for m in matches if m.symbol == symbol), None)
    if info is None:
        raise NotFoundError(f"{market} 市場查無代號 {symbol}")

    stock = Stock(
        symbol=info.symbol, market=market, name=info.name,
        currency=info.currency, kind=info.kind,
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _load_last_price_date(stock_id: int) -> date | None:
    with SessionLocal() as db:
        return db.execute(
            select(DailyPrice.date)
            .where(DailyPrice.stock_id == stock_id)
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one_or_none()


def _persist_price_rows(stock_id: int, rows: list[OhlcvRow]) -> int:
    with SessionLocal() as db:
        dates = [row.date for row in rows]
        existing_rows = (
            db.execute(
                select(DailyPrice).where(
                    DailyPrice.stock_id == stock_id,
                    DailyPrice.date.in_(dates),
                )
            ).scalars().all()
            if dates
            else []
        )
        existing_by_date = {row.date: row for row in existing_rows}
        changed = 0
        for row in rows:
            existing = existing_by_date.get(row.date)
            values = {
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            if existing is None:
                db.add(DailyPrice(stock_id=stock_id, date=row.date, **values))
                changed += 1
                continue
            before = (
                _clean_number(existing.open),
                _clean_number(existing.high),
                _clean_number(existing.low),
                _clean_number(existing.close),
                existing.volume,
            )
            if before != (row.open, row.high, row.low, row.close, row.volume):
                for key, value in values.items():
                    setattr(existing, key, value)
                changed += 1
        if changed:
            db.flush()
            stock = db.get(Stock, stock_id)
            if stock is None:
                raise NotFoundError(f"Stock not found: id={stock_id}")
            _recompute_indicators(db, stock)
        db.commit()
        return changed


async def sync_prices(stock_id: int, market: str, symbol: str) -> int:
    """增量同步日線並重算指標，回傳新增或更新筆數。"""
    last = await asyncio.to_thread(_load_last_price_date, stock_id)
    today = market_today(market)
    start = (
        last - timedelta(days=REFRESH_LOOKBACK_DAYS)
        if last
        else today - timedelta(days=INITIAL_LOOKBACK_DAYS)
    )
    if start > today:
        return 0

    rows = await market_data.get_daily_prices(market, symbol, start, today)
    changed = await asyncio.to_thread(_persist_price_rows, stock_id, rows)
    logger.info("synced %s/%s: %d rows changed", market, symbol, changed)
    return changed


def _clean_number(value) -> float | None:
    return float(value) if value is not None else None


def _recompute_indicators(db: Session, stock: Stock) -> None:
    """指標整段重算（資料量小，全量重算比補丁簡單可靠）。"""
    price_rows = db.execute(
        select(DailyPrice).where(DailyPrice.stock_id == stock.id).order_by(DailyPrice.date)
    ).scalars().all()
    if not price_rows:
        return

    df = pd.DataFrame(
        {
            "date": [p.date for p in price_rows],
            "open": [float(p.open or 0) for p in price_rows],
            "high": [float(p.high or 0) for p in price_rows],
            "low": [float(p.low or 0) for p in price_rows],
            "close": [float(p.close or 0) for p in price_rows],
            "volume": [p.volume or 0 for p in price_rows],
        }
    )
    indicators = compute_indicators(df)

    db.execute(delete(Indicator).where(Indicator.stock_id == stock.id))
    for _, row in indicators.iterrows():
        db.add(
            Indicator(
                stock_id=stock.id,
                date=row["date"],
                ma5=_clean(row["ma5"]), ma20=_clean(row["ma20"]), ma60=_clean(row["ma60"]),
                rsi14=_clean(row["rsi14"]),
                kd_k=_clean(row["kd_k"]), kd_d=_clean(row["kd_d"]),
                macd=_clean(row["macd"]), macd_signal=_clean(row["macd_signal"]),
                bb_upper=_clean(row["bb_upper"]), bb_lower=_clean(row["bb_lower"]),
            )
        )
