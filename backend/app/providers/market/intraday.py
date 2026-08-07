"""盤中即時報價（僅供出場哨兵使用，量小、免 key）。

台股：證交所 mis.twse.com.tw 官方即時端點（上市 tse_ 與上櫃 otc_ 一次並查）
美股：Finnhub（API key 辨識呼叫者，機房 IP 可用）
抓不到的標的直接略過（回傳字典缺鍵），哨兵端視為「本輪不檢查」；
若一檔都取不到，哨兵會讓該輪算失敗（見 sim/sentinel），不再靜默跳過。
"""
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
    """唯一來源為 Finnhub（API key 辨識呼叫者，機房 IP 可用）。

    曾以 yfinance 作備援，但 Yahoo 以 IP 信譽封鎖機房來源，正式環境上
    它是「必定失敗的備援」而非「偶爾失敗的備援」：2026-08-06 18:40 的
    哨兵 5 檔全數退到 yfinance、全部收到 429，當輪停損停利完全失效，
    還白花 5 次請求與數秒延遲。取不到就是取不到——哨兵那端已會如實
    讓該輪算失敗（見 sim/sentinel），不需要一個只會粉飾的備援。
    """
    from app.providers.market import finnhub

    return await finnhub.fetch_quotes(symbols)
