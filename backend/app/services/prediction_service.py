"""走勢預測：線性回歸通道（可解釋、無外部依賴）。

以近 60 個收盤做線性回歸，向前投影 5/20 個交易日，
帶寬 = ±2 × 殘差標準差。同一交易日結果快取於 predictions 表。
UI 一律以「區間帶」呈現，絕不給單點預測。
"""
import json
import math
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Prediction, Stock
from app.services.trading_calendar import next_trading_dates

LOOKBACK = 60
HORIZONS = (5, 20)
METHOD = "regression_channel"


def get_predictions(db: Session, stock: Stock) -> dict:
    prices = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id == stock.id)
        .order_by(DailyPrice.date.desc())
        .limit(LOOKBACK)
    ).scalars().all()
    if len(prices) < 30:
        raise NotFoundError(f"{stock.symbol} 資料不足（<30 筆），無法預測")
    prices = list(reversed(prices))
    trade_date = prices[-1].date

    cached = db.execute(
        select(Prediction).where(
            Prediction.stock_id == stock.id,
            Prediction.trade_date == trade_date,
            Prediction.method == METHOD,
        )
    ).scalars().all()
    if cached:
        return _dto(trade_date, {p.horizon_days: json.loads(p.predicted_json) for p in cached})

    closes = [float(p.close) for p in prices if p.close is not None]
    slope, intercept, sigma = _fit(closes)
    n = len(closes)

    result: dict[int, list[dict]] = {}
    for horizon in HORIZONS:
        band = []
        for step, future_date in enumerate(
            next_trading_dates(stock.market, trade_date, horizon), start=1
        ):
            mid = intercept + slope * (n - 1 + step)
            band.append(
                {
                    "date": future_date.isoformat(),
                    "mid": round(mid, 2),
                    "upper": round(mid + 2 * sigma, 2),
                    "lower": round(max(0.0, mid - 2 * sigma), 2),
                }
            )
        db.add(
            Prediction(
                stock_id=stock.id,
                trade_date=trade_date,
                horizon_days=horizon,
                method=METHOD,
                predicted_json=json.dumps(band),
            )
        )
        result[horizon] = band
    db.commit()
    return _dto(trade_date, result)


def _fit(values: list[float]) -> tuple[float, float, float]:
    """最小平方法：回傳 (slope, intercept, 殘差標準差)。"""
    n = len(values)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    slope = ss_xy / ss_xx if ss_xx else 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, values)]
    sigma = math.sqrt(sum(r * r for r in residuals) / (n - 2)) if n > 2 else 0.0
    return slope, intercept, sigma


def _dto(trade_date: date, by_horizon: dict[int, list[dict]]) -> dict:
    return {
        "trade_date": trade_date.isoformat(),
        "method": METHOD,
        "horizons": {str(h): band for h, band in by_horizon.items()},
        "disclaimer": "統計投影僅供參考，非投資建議",
    }
