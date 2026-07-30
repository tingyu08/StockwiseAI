"""Finnhub 美股即時報價（盤中出場哨兵專用）。

為什麼需要它：Yahoo（yfinance）以 IP 信譽封鎖機房來源，正式環境的美股持倉
從「偶爾一檔取不到」惡化到「整批全滅」——停損/停利實質完全失效，而那是
真的會影響部位的東西。

Finnhub 是以 API key 辨識呼叫者的正式 API，不是給瀏覽器用的爬蟲端點，
機房 IP 不受影響——這是它跟 Yahoo 的關鍵差別。免費層 60 req/分鐘，
而哨兵每小時只查數檔，額度綽綽有餘。

未設定 token 時回傳空字典，由呼叫端退回 yfinance（維持原行為）。
"""
import asyncio
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

QUOTE_URL = "https://finnhub.io/api/v1/quote"
TIMEOUT_SEC = 10

_missing_token_warned = False


async def fetch_quotes(symbols: list[str]) -> dict[str, float]:
    """取美股現價。取不到的標的不會出現在回傳字典裡（呼叫端據此退備援）。"""
    if not symbols:
        return {}
    token = get_settings().finnhub_token.strip()
    if not token:
        _warn_missing_token_once()
        return {}

    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        results = await asyncio.gather(
            *(_one(client, symbol, token) for symbol in symbols)
        )
    return {symbol: price for symbol, price in zip(symbols, results) if price is not None}


def _warn_missing_token_once() -> None:
    """只警告一次：哨兵每小時跑一輪，每輪都喊會把 log 淹掉。"""
    global _missing_token_warned
    if not _missing_token_warned:
        _missing_token_warned = True
        logger.warning(
            "未設定 FINNHUB_TOKEN，美股盤中報價只能退回 yfinance"
            "（Yahoo 對機房 IP 限流，停損可能失效）"
        )


async def _one(client: httpx.AsyncClient, symbol: str, token: str) -> float | None:
    try:
        res = await client.get(QUOTE_URL, params={"symbol": symbol, "token": token})
    except httpx.HTTPError as exc:
        logger.warning("Finnhub 報價 %s 連線失敗：%s", symbol, exc)
        return None
    if res.status_code == 429:
        logger.warning("Finnhub 報價 %s 被限流（429）", symbol)
        return None
    if res.status_code != 200:
        logger.warning("Finnhub 報價 %s 失敗（HTTP %s）", symbol, res.status_code)
        return None
    try:
        payload = res.json()
    except ValueError:
        logger.warning("Finnhub 報價 %s 回傳非 JSON", symbol)
        return None
    # 'c' = current price。未知代號時 Finnhub 回傳整組 0，
    # 不檢查 >0 會把 0 當成成交價餵給哨兵，直接誤觸發停損。
    price = payload.get("c")
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
