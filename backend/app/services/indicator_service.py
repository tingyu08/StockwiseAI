"""技術指標計算 — 純函式、無 IO，輸入輸出皆為新物件（不 mutate）。

輸入 DataFrame 需含欄位：date, open, high, low, close, volume（依日期升冪）。
"""
import pandas as pd


def compute_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    """回傳新 DataFrame：date + 各指標欄位。資料不足的期間為 NaN。"""
    if prices.empty:
        return pd.DataFrame(
            columns=[
                "date", "ma5", "ma20", "ma60", "rsi14",
                "kd_k", "kd_d", "macd", "macd_signal", "bb_upper", "bb_lower",
            ]
        )

    df = prices.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    out = pd.DataFrame({"date": df["date"]})
    out["ma5"] = close.rolling(5).mean()
    out["ma20"] = close.rolling(20).mean()
    out["ma60"] = close.rolling(60).mean()

    # RSI(14) — Wilder smoothing；用 100*gain/(gain+loss) 等價式避免 loss=0 除零
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    denom = gain + loss
    out["rsi14"] = (100 * gain / denom.where(denom > 0)).astype(float)

    # KD(9,3,3)；區間為 0（連續平盤）時 RSV 視為 NaN
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    span = high9 - low9
    rsv = ((close - low9) / span.where(span > 0) * 100).astype(float)
    out["kd_k"] = rsv.ewm(alpha=1 / 3, min_periods=1).mean()
    out["kd_d"] = out["kd_k"].ewm(alpha=1 / 3, min_periods=1).mean()

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, min_periods=12).mean()
    ema26 = close.ewm(span=26, min_periods=26).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, min_periods=9).mean()

    # Bollinger(20, 2)
    ma20 = out["ma20"]
    std20 = close.rolling(20).std()
    out["bb_upper"] = ma20 + 2 * std20
    out["bb_lower"] = ma20 - 2 * std20

    return out
