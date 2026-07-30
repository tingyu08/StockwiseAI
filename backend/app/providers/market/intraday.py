"""盤中即時報價（僅供出場哨兵使用，量小、免 key）。

台股：證交所 mis.twse.com.tw 官方即時端點（上市 tse_ 與上櫃 otc_ 一次並查）
美股：Finnhub 為主（API key 辨識，機房 IP 可用），yfinance 為備援
抓不到的標的直接略過（回傳字典缺鍵），哨兵端視為「本輪不檢查」；
若一檔都取不到，哨兵會讓該輪算失敗（見 sim/sentinel），不再靜默跳過。
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
        if not symbol:
            continue
        # z=最新成交價；無成交時退最佳買價 b（哨兵只做賣出，買價即可成交價）。
        # 兩者皆空＝當下賣不掉（如跌停鎖死買盤空），跳過是「擬真」的正確行為。
        price = _parse_price(row.get("z")) or _parse_price(row.get("b"))
        if price:
            quotes[symbol] = price
        else:
            logger.info(
                "TWSE 盤中無可成交價 %s：z=%r b=%r a=%r t=%r（可能跌停鎖死/暫停交易）",
                symbol, row.get("z"), row.get("b"), row.get("a"), row.get("t"),
            )
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
    """Finnhub 為主、yfinance 為備援。

    主從順序是刻意的：Yahoo 以 IP 信譽封鎖機房來源，正式環境曾整批取不到
    報價、停損完全失效；Finnhub 以 API key 辨識呼叫者，不看 IP。
    未設定 FINNHUB_TOKEN 時 fetch_quotes 回空字典，等於全數走 yfinance。
    """
    from app.providers.market import finnhub

    quotes = await finnhub.fetch_quotes(symbols)
    missing = [s for s in symbols if s not in quotes]
    if missing:
        quotes.update(await _us_quotes_via_yfinance(missing))
    return quotes


async def _us_quotes_via_yfinance(symbols: list[str]) -> dict[str, float]:
    import yfinance as yf

    from app.providers.market.yf_cache import yfinance_guard

    def _one(symbol: str) -> float | None:
        try:
            # 序列化：yfinance 的時區快取是 SQLite，併發首次查詢會撞
            # database is locked（見 yf_cache._YF_LOCK）
            with yfinance_guard():
                price = yf.Ticker(symbol).fast_info["last_price"]
            return float(price) if price and price > 0 else None
        except Exception as exc:
            logger.warning("yfinance 即時報價 %s 失敗：%s", symbol, exc)
            return None

    results = await asyncio.gather(*(asyncio.to_thread(_one, s) for s in symbols))
    return {s: p for s, p in zip(symbols, results) if p is not None}
