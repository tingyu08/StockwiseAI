from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Indicator, Stock
from app.services.time_service import market_today

RANGE_DAYS = {"3m": 90, "6m": 180, "1y": 365}


def get_stock(db: Session, market: str, symbol: str) -> Stock:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"Stock not found: {market}/{symbol}")
    return stock


def stock_dto(stock: Stock) -> dict:
    return {
        "symbol": stock.symbol,
        "market": stock.market,
        "name": stock.name,
        "currency": stock.currency,
        "kind": stock.kind,
        "tracked": True,
    }


def get_price_series(db: Session, stock: Stock, range_key: str) -> dict:
    since = market_today(stock.market) - timedelta(days=RANGE_DAYS[range_key])
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
    by_date = {row.date: row for row in indicators}
    series = []
    for price in prices:
        indicator = by_date.get(price.date)
        series.append(
            {
                "date": price.date.isoformat(),
                "open": _num(price.open),
                "high": _num(price.high),
                "low": _num(price.low),
                "close": _num(price.close),
                "volume": price.volume,
                "ma5": _num(indicator.ma5) if indicator else None,
                "ma20": _num(indicator.ma20) if indicator else None,
                "ma60": _num(indicator.ma60) if indicator else None,
                "rsi14": _num(indicator.rsi14) if indicator else None,
                "kd_k": _num(indicator.kd_k) if indicator else None,
                "kd_d": _num(indicator.kd_d) if indicator else None,
                "macd": _num(indicator.macd) if indicator else None,
                "macd_signal": _num(indicator.macd_signal) if indicator else None,
                "bb_upper": _num(indicator.bb_upper) if indicator else None,
                "bb_lower": _num(indicator.bb_lower) if indicator else None,
            }
        )
    return {"stock": stock_dto(stock), "series": series}


def _num(value) -> float | None:
    return float(value) if value is not None else None
