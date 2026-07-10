"""資料同步：外部源 → 本地 DB（增量抓價 → 重算指標 → upsert）。"""
import logging
import math
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Indicator, Stock
from app.services.indicator_service import compute_indicators
from app.services.market_gateway import market_data

logger = logging.getLogger(__name__)

INITIAL_LOOKBACK_DAYS = 400  # 首次同步抓約 400 天（足夠算 MA60 且涵蓋一年走勢）


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


async def sync_prices(db: Session, stock: Stock) -> int:
    """增量同步日線並重算指標，回傳新增筆數。"""
    last: date | None = db.execute(
        select(DailyPrice.date)
        .where(DailyPrice.stock_id == stock.id)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    start = (last + timedelta(days=1)) if last else date.today() - timedelta(days=INITIAL_LOOKBACK_DAYS)
    today = date.today()
    if start > today:
        return 0

    rows = await market_data.get_daily_prices(stock.market, stock.symbol, start, today)
    new_rows = [r for r in rows if last is None or r.date > last]
    for r in new_rows:
        db.add(
            DailyPrice(
                stock_id=stock.id, date=r.date,
                open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume,
            )
        )
    if new_rows:
        db.flush()  # session 為 autoflush=False：先 flush 讓指標重算查得到新價格
        _recompute_indicators(db, stock)
    db.commit()
    logger.info("synced %s/%s: +%d rows", stock.market, stock.symbol, len(new_rows))
    return len(new_rows)


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
