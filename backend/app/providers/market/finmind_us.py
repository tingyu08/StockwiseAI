"""FinMind 美股/指數日線（yfinance 的備援）。

Yahoo 對雲端機房 IP 常限流；Stooq 已上 JS 反機器人驗證無法程式取用。
FinMind 的 USStockPrice 資料集支援一般美股代號與原生指數代號
（^GSPC、^SOX、TSM 實測皆可），台股加權指數走 TaiwanStockPrice/TAIEX。
同步函式（呼叫端以 asyncio.to_thread 包裝），與 FinMindProvider 共用 token。
"""
import logging
from datetime import date, timedelta

import httpx
import pandas as pd

from app.core.config import get_settings
from app.services.time_service import market_today

logger = logging.getLogger(__name__)

API_URL = "https://api.finmindtrade.com/api/v4/data"
DEFAULT_LOOKBACK_DAYS = 200  # API 必帶 start_date；未指定時取近 200 天


def fetch_stock_info(symbol: str) -> dict | None:
    """USStockInfo：回傳 {name, kind}，查無回 None。

    Subsector == "ETF" 是 FinMind 區分 ETF 與個股的唯一依據（實測：ETF 的
    Country/IPOYear/MarketCap 皆為空值，只有 Subsector 有意義）。
    """
    try:
        res = httpx.get(
            API_URL,
            params={
                "dataset": "USStockInfo",
                "data_id": symbol,
                "token": get_settings().finmind_token,
                "start_date": "2020-01-01",
            },
            timeout=30,
        )
        body = res.json()
    except Exception as exc:
        logger.warning("FinMind USStockInfo %s 失敗：%s", symbol, exc)
        return None
    if res.status_code != 200 or body.get("status") != 200:
        logger.warning("FinMind USStockInfo %s 回應異常：%s", symbol, body.get("msg"))
        return None
    rows = body.get("data") or []
    if not rows:
        return None
    row = rows[-1]  # 取最新一筆
    subsector = (row.get("Subsector") or "").strip()
    return {
        "name": (row.get("stock_name") or symbol).strip() or symbol,
        "kind": "etf" if subsector.upper() == "ETF" else "stock",
    }


def fetch_daily(symbol: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """回傳含 Date/Open/High/Low/Close/Volume 的 DataFrame（升冪），查無回空。"""
    if symbol == "^TWII":
        dataset, data_id = "TaiwanStockPrice", "TAIEX"
    else:
        dataset, data_id = "USStockPrice", symbol

    params: dict = {
        "dataset": dataset,
        "data_id": data_id,
        "token": get_settings().finmind_token,
        "start_date": (
            start or market_today("US") - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        ).isoformat(),
    }
    if end:
        params["end_date"] = end.isoformat()

    res = httpx.get(API_URL, params=params, timeout=30)
    body = res.json()
    if res.status_code != 200 or body.get("status") != 200:
        logger.warning("FinMind %s/%s 回應異常：%s", dataset, data_id, body.get("msg"))
        return pd.DataFrame()
    rows = body.get("data", [])
    if not rows:
        return pd.DataFrame()

    if dataset == "TaiwanStockPrice":
        df = pd.DataFrame(
            {
                "Date": [r["date"] for r in rows],
                "Open": [r.get("open") for r in rows],
                "High": [r.get("max") for r in rows],
                "Low": [r.get("min") for r in rows],
                "Close": [r.get("close") for r in rows],
                "Volume": [r.get("Trading_Volume") for r in rows],
            }
        )
    else:
        df = pd.DataFrame(rows)[["date", "Open", "High", "Low", "Close", "Volume"]].rename(
            columns={"date": "Date"}
        )
    df["Date"] = pd.to_datetime(df["Date"])
    df["Volume"] = df["Volume"].fillna(0)
    return df.sort_values("Date").reset_index(drop=True)
