"""盤中即時報價（僅供出場哨兵使用，量小、免 key）。

台股：證交所 mis.twse.com.tw 官方即時端點（上市 tse_ 與上櫃 otc_ 一次並查）
美股：yfinance fast_info（延遲報價，對小時級哨兵足夠）
抓不到的標的直接略過（回傳字典缺鍵），哨兵端視為「本輪不檢查」。
"""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

TW_QUOTE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


async def fetch_intraday_quotes(market: str, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    if market == "TW":
        return await _tw_quotes(symbols)
    return await _us_quotes(symbols)


async def _tw_quotes(symbols: list[str]) -> dict[str, float]:
    # 不知道個股屬上市或上櫃 → 兩個頻道都查，取有回報價的那個
    ex_ch = "|".join(f"{ex}_{s}.tw" for s in symbols for ex in ("tse", "otc"))
    try:
        async with httpx.AsyncClient(
            timeout=20, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            res = await client.get(
                TW_QUOTE_URL, params={"ex_ch": ex_ch, "json": "1", "delay": "0"}
            )
            res.raise_for_status()
            body = res.json()
    except Exception as exc:
        logger.warning("TWSE 即時報價失敗：%s", exc)
        return {}

    quotes: dict[str, float] = {}
    for row in body.get("msgArray", []):
        symbol = row.get("c")
        price = _parse_price(row.get("z")) or _parse_price(row.get("b"))
        if symbol and price:
            quotes[symbol] = price
    return quotes


def _parse_price(raw: str | None) -> float | None:
    """'z' 為最新成交價；無成交時為 '-'，退而取最佳買價 'b' 的第一檔。"""
    if not raw or raw == "-":
        return None
    first = raw.split("_")[0]
    try:
        value = float(first)
        return value if value > 0 else None
    except ValueError:
        return None


async def _us_quotes(symbols: list[str]) -> dict[str, float]:
    import yfinance as yf

    def _one(symbol: str) -> float | None:
        try:
            price = yf.Ticker(symbol).fast_info["last_price"]
            return float(price) if price and price > 0 else None
        except Exception as exc:
            logger.warning("yfinance 即時報價 %s 失敗：%s", symbol, exc)
            return None

    results = await asyncio.gather(*(asyncio.to_thread(_one, s) for s in symbols))
    return {s: p for s, p in zip(symbols, results) if p is not None}
