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
# 逾時重試一次。哨兵是分鐘級的東西，退避只取一秒——重點是跨過一次網路抖動，
# 不是等到上游痊癒。主來源一次逾時就整批掉到 yfinance 的代價太高：
# Yahoo 對機房 IP 回 429，等於當輪停損完全失效（2026-08-06 18:40 實例）。
RETRY_DELAY_SEC = 1.0
_sleep = asyncio.sleep

_missing_token_warned = False


async def fetch_quotes(symbols: list[str]) -> dict[str, float]:
    """取美股現價。取不到的標的不會出現在回傳字典裡（呼叫端據此退備援）。"""
    if not symbols:
        return {}
    token = get_settings().finnhub_token.strip()
    if not token:
        _warn_missing_token_once()
        return {}

    # 金鑰走 header 而非 query string：URL 會被 httpx 的 INFO log、反向代理與
    # 平台 access log 原樣記下（本地遮蔽擋不到那一層）。Finnhub 官方同樣建議
    # 用 header，理由一致——見 finnhubio/Finnhub-API#301。與 gemini provider 的作法一致。
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SEC, headers={"X-Finnhub-Token": token}
    ) as client:
        results = await asyncio.gather(*(_one(client, symbol) for symbol in symbols))
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


async def _get_with_retry(
    client: httpx.AsyncClient, symbol: str
) -> httpx.Response | None:
    """逾時重試一次；其餘傳輸錯誤（連線被拒、TLS 失敗）是確定性的，不重試。

    log 一律記下例外型別：httpx 的逾時例外 str() 是空字串，只印 exc 會得到
    「連線失敗：」這種完全無法診斷的訊息（2026-08-06 的正式 log 即如此）。
    """
    for attempt in (1, 2):
        try:
            return await client.get(QUOTE_URL, params={"symbol": symbol})
        except httpx.TimeoutException as exc:
            logger.warning(
                "Finnhub 報價 %s 逾時（第 %d 次，%s）",
                symbol, attempt, type(exc).__name__,
            )
            if attempt == 1:
                await _sleep(RETRY_DELAY_SEC)
        except httpx.HTTPError as exc:
            logger.warning(
                "Finnhub 報價 %s 連線失敗（%s）：%s", symbol, type(exc).__name__, exc
            )
            return None
    return None


async def _one(client: httpx.AsyncClient, symbol: str) -> float | None:
    res = await _get_with_retry(client, symbol)
    if res is None:
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
