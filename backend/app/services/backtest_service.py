"""規則策略回測引擎（自製輕量版，不進 AI——AI 策略用模擬交易前進測試驗證）。

規則：
- 訊號於收盤產生，次一交易日「開盤價」進出場（與模擬引擎一致，避免前視偏誤）
- 全倉進出，費率沿用模擬引擎的常數（台股手續費＋證交稅、美股 0），
  ETF 的證交稅為 0.1%（個股 0.3%）
- 指標：總報酬、年化、最大回撤、勝率、交易數、對比買入持有

已知簡化：權益以 1.0 正規化，沒有真實幣別金額，因此模擬引擎的
「手續費最低 20 元」無法在此表達（那需要先假定一筆起始本金）。
小額交易的實際成本會略高於本回測。
"""
from dataclasses import dataclass
from datetime import timedelta
import math

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Stock
from app.services.indicator_service import compute_indicators
from app.services.sim.engine import TW_FEE_RATE, tw_tax_rate
from app.services.time_service import market_today

TRADING_DAYS = 252

STRATEGIES = {
    "ma_cross": "MA5/MA20 黃金交叉買進、死亡交叉賣出",
    "rsi_reversion": "RSI14 < 30 買進、> 70 賣出（均值回歸）",
    "bollinger": "收盤跌破布林下軌買進、觸及上軌賣出",
}


@dataclass(frozen=True)
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str | None
    exit_price: float | None
    pnl_pct: float | None


def run_backtest(
    db: Session, market: str, symbol: str, strategy: str, range_days: int = 365,
    slippage_bps: int = 5,
) -> dict:
    if strategy not in STRATEGIES:
        raise NotFoundError(f"未知策略：{strategy}（可用：{', '.join(STRATEGIES)}）")
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"尚未追蹤 {market}/{symbol}")

    since = market_today(market) - timedelta(days=range_days + 120)
    rows = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id == stock.id, DailyPrice.date >= since)
        .order_by(DailyPrice.date)
    ).scalars().all()
    rows = [r for r in rows if r.close is not None and r.open is not None]
    if len(rows) < 80:
        raise NotFoundError(f"{symbol} 資料不足（{len(rows)} 筆，需 80+）")

    df = pd.DataFrame(
        {
            "date": [r.date for r in rows],
            "open": [float(r.open) for r in rows],
            "high": [float(r.high) for r in rows],
            "low": [float(r.low) for r in rows],
            "close": [float(r.close) for r in rows],
            "volume": [r.volume or 0 for r in rows],
        }
    )
    ind = compute_indicators(df)
    signals = _signals(strategy, df, ind)  # 每日目標持倉 0/1（收盤時決定）

    # 上面多抓的 120 天只是暖身，讓 MA60/RSI 等指標在視窗起點就有效；
    # 績效必須只涵蓋使用者要求的 range_days，否則 annualized/sharpe/
    # buy_hold/period 全都落在比要求更長的區間，且前段指標尚未生效、
    # 持倉恆為 0 的平盤日會系統性拉低年化與 Sharpe。
    window_start = market_today(market) - timedelta(days=range_days)
    mask = (df["date"] >= window_start).values
    if mask.any():
        first = int(mask.argmax())
        df = df.iloc[first:].reset_index(drop=True)
        signals = signals[first:]
    if len(df) < 2:
        raise NotFoundError(f"{symbol} 於指定區間資料不足（{len(df)} 筆）")

    return _simulate(
        market, df, signals, strategy,
        slippage_bps=slippage_bps, is_etf=stock.kind == "etf",
    )


def _signals(strategy: str, df: pd.DataFrame, ind: pd.DataFrame) -> list[int]:
    """回傳與 df 等長的 0/1 目標持倉序列；訊號不明時延續前一日狀態。"""
    n = len(df)
    signals = [0] * n
    state = 0
    for i in range(n):
        buy = sell = False
        if strategy == "ma_cross":
            ma5, ma20 = ind["ma5"].iloc[i], ind["ma20"].iloc[i]
            if not (pd.isna(ma5) or pd.isna(ma20)):
                buy, sell = ma5 > ma20, ma5 < ma20
        elif strategy == "rsi_reversion":
            rsi = ind["rsi14"].iloc[i]
            if not pd.isna(rsi):
                buy, sell = rsi < 30, rsi > 70
        elif strategy == "bollinger":
            lower, upper = ind["bb_lower"].iloc[i], ind["bb_upper"].iloc[i]
            close = df["close"].iloc[i]
            if not (pd.isna(lower) or pd.isna(upper)):
                buy, sell = close < lower, close >= upper
        if state == 0 and buy:
            state = 1
        elif state == 1 and sell:
            state = 0
        signals[i] = state
    return signals


def _simulate(
    market: str, df: pd.DataFrame, signals: list[int], strategy: str,
    slippage_bps: int = 0, is_etf: bool = False,
) -> dict:
    cash = 1.0  # 正規化資金
    qty = 0.0
    equity_curve: list[dict] = []
    trades: list[Trade] = []
    entry: tuple[str, float] | None = None

    n = len(df)
    for i in range(n):
        # 依「昨日收盤訊號」於今日開盤調整持倉
        if i > 0:
            target = signals[i - 1]
            open_price = df["open"].iloc[i]
            if target == 1 and qty == 0:
                execution_price = open_price * (1 + slippage_bps / 10_000)
                qty = cash * (1 - _fee_rate(market, "buy", is_etf)) / execution_price
                cash = 0.0
                entry = (df["date"].iloc[i].isoformat(), execution_price)
            elif target == 0 and qty > 0:
                execution_price = open_price * (1 - slippage_bps / 10_000)
                gross = qty * execution_price
                cash = gross * (1 - _fee_rate(market, "sell", is_etf))
                trades.append(Trade(
                    entry[0], entry[1], df["date"].iloc[i].isoformat(), execution_price,
                    _net_pnl_pct(market, entry[1], execution_price, is_etf),
                ))
                qty = 0.0
                entry = None
        close = df["close"].iloc[i]
        equity_curve.append(
            {"date": df["date"].iloc[i].isoformat(), "equity": round(cash + qty * close, 6)}
        )

    open_position = None
    if entry is not None:
        # 期末未平倉＝逐日盯市，與 equity_curve 同一把尺（只含已付出的
        # 買進成本）。若在此扣賣出費，就會變成第三種定義：既不等於
        # equity 也不等於已平倉交易的淨值，反而更難解讀。
        last_close = df["close"].iloc[-1]
        buy_fee = _fee_rate(market, "buy", is_etf)
        open_position = Trade(
            entry[0], entry[1], None, None,
            round(((1 - buy_fee) * last_close / entry[1] - 1) * 100, 2),
        )

    equities = [p["equity"] for p in equity_curve]
    total_return = (equities[-1] - 1) * 100
    years = max(len(df) / TRADING_DAYS, 1 / TRADING_DAYS)
    # 買入持有也要含費，否則 beats_buy_hold 是「淨 vs 毛」相減：
    # 策略被多扣了成本，基準卻沒有，超額報酬會被系統性低估
    buy_hold = _net_pnl_pct(
        market, df["open"].iloc[0], df["close"].iloc[-1], is_etf
    )
    closed = [t for t in trades if t.exit_date is not None]
    wins = [t for t in closed if t.pnl_pct > 0]

    return {
        "strategy": strategy,
        "strategy_desc": STRATEGIES[strategy],
        "period": {"start": df["date"].iloc[0].isoformat(), "end": df["date"].iloc[-1].isoformat()},
        "metrics": {
            "total_return_pct": round(total_return, 2),
            "annualized_pct": round(((equities[-1]) ** (1 / years) - 1) * 100, 2)
            if equities[-1] > 0 else None,
            "max_drawdown_pct": round(_max_drawdown(equities) * 100, 2),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "trades": len(closed),
            "buy_hold_return_pct": round(buy_hold, 2),
            "beats_buy_hold": round(total_return - buy_hold, 2),
            "sharpe_ratio": _sharpe_ratio(equities),
        },
        "assumptions": {
            "slippage_bps": slippage_bps,
            # 損益/勝率都是含費淨值，UI 需揭露否則使用者拿進出場價自算會對不上
            "buy_fee_pct": round(_fee_rate(market, "buy", is_etf) * 100, 4),
            "sell_fee_pct": round(_fee_rate(market, "sell", is_etf) * 100, 4),
        },
        "equity_curve": equity_curve,
        "trades": [t.__dict__ for t in closed[-50:]],
        "open_position": open_position.__dict__ if open_position else None,
        "disclaimer": "回測基於歷史資料與簡化假設，不代表未來績效",
    }


def _net_pnl_pct(
    market: str, entry_price: float, exit_price: float, is_etf: bool = False
) -> float:
    """單筆含手續費的淨報酬率（%）。

    毛報酬會讓 win_rate 與 equity 曲線不一致：台股單筆來回約 0.585%
    （買 0.1425% ＋ 賣 0.4425%）以內的正毛報酬其實是淨虧損，卻會被
    算成一筆勝場。equity 曲線本來就是含費淨值，兩者必須同一把尺。
    """
    buy_fee = _fee_rate(market, "buy", is_etf)
    sell_fee = _fee_rate(market, "sell", is_etf)
    net_multiple = (1 - buy_fee) * (1 - sell_fee) * exit_price / entry_price
    return round((net_multiple - 1) * 100, 2)


def _fee_rate(market: str, side: str, is_etf: bool = False) -> float:
    """費率沿用 sim/engine 的常數，避免兩套引擎各自漂移。"""
    if market == "US":
        return 0.0
    if side == "sell":
        return TW_FEE_RATE + tw_tax_rate(is_etf)
    return TW_FEE_RATE


def _max_drawdown(equities: list[float]) -> float:
    peak = equities[0]
    mdd = 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd


def _sharpe_ratio(equities: list[float]) -> float | None:
    if len(equities) < 3:
        return None
    returns = [
        equities[i] / equities[i - 1] - 1
        for i in range(1, len(equities))
        if equities[i - 1] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    if variance <= 0:
        return None
    return round(mean / math.sqrt(variance) * math.sqrt(TRADING_DAYS), 3)
