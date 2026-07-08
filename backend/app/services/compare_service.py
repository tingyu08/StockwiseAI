"""多股報酬率比較：指標表格＋正規化序列（各股以區間首日=100）。"""
import math
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Stock

RANGE_DAYS = {"3m": 90, "6m": 180, "1y": 365}
TRADING_DAYS_PER_YEAR = 252


def compare(db: Session, market: str, symbols: list[str], range_key: str) -> dict:
    since = date.today() - timedelta(days=RANGE_DAYS[range_key])
    rows = []
    series_map: dict[str, list[dict]] = {}

    for symbol in symbols:
        stock = db.execute(
            select(Stock).where(Stock.market == market, Stock.symbol == symbol)
        ).scalar_one_or_none()
        if stock is None:
            raise NotFoundError(f"尚未追蹤 {market}/{symbol}")

        prices = db.execute(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id, DailyPrice.date >= since)
            .order_by(DailyPrice.date)
        ).scalars().all()
        closes = [(p.date, float(p.close)) for p in prices if p.close is not None]
        if len(closes) < 2:
            raise NotFoundError(f"{symbol} 於此區間資料不足")

        values = [c for _, c in closes]
        rows.append(
            {
                "symbol": stock.symbol,
                "name": stock.name,
                "kind": stock.kind,
                "return_1w": _trailing_return(values, 5),
                "return_1m": _trailing_return(values, 21),
                "return_3m": _trailing_return(values, 63),
                "return_ytd": _ytd_return(closes),
                "annualized_return": _annualized(values),
                "volatility": _volatility(values),
                "last_close": values[-1],
            }
        )
        base = values[0]
        series_map[stock.symbol] = [
            {"date": d.isoformat(), "value": round(c / base * 100, 2)} for d, c in closes
        ]

    return {"metrics": rows, "series": series_map}


def _trailing_return(values: list[float], days: int) -> float | None:
    if len(values) <= days:
        return None
    base = values[-days - 1]
    return round((values[-1] - base) / base * 100, 2) if base else None


def _ytd_return(closes: list[tuple[date, float]]) -> float | None:
    year_start = date(date.today().year, 1, 1)
    base_points = [c for d, c in closes if d >= year_start]
    if len(base_points) < 2:
        return None
    return round((base_points[-1] - base_points[0]) / base_points[0] * 100, 2)


def _annualized(values: list[float]) -> float | None:
    n = len(values) - 1
    if n < 20 or values[0] <= 0:
        return None
    total = values[-1] / values[0]
    if total <= 0:
        return None
    return round((total ** (TRADING_DAYS_PER_YEAR / n) - 1) * 100, 2)


def _volatility(values: list[float]) -> float | None:
    """年化波動率（%）。"""
    if len(values) < 21:
        return None
    returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1]
    ]
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2)
