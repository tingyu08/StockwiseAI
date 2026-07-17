"""市場環境資料：全球指數、台積電 ADR、加權指數技術位階（yfinance，免費）。

供每日簡報的「全球盤勢」與「大盤預判」模組使用——後端抓真實數據餵 AI，
AI 只負責解讀，不虛構行情。
"""
import asyncio
import logging
from dataclasses import dataclass

import httpx
import pandas as pd
import yfinance as yf

from app.providers.market import finmind_us

logger = logging.getLogger(__name__)

GLOBAL_TICKERS = {
    "^GSPC": "S&P 500",
    "^IXIC": "那斯達克",
    "^DJI": "道瓊工業",
    "^SOX": "費城半導體",
}
ADR_TICKER = ("TSM", "台積電 ADR")
LOCAL_INDEX = {"TW": ("^TWII", "加權指數"), "US": ("^GSPC", "S&P 500")}


@dataclass(frozen=True)
class IndexQuote:
    name: str
    close: float
    change_pct: float


def _history(ticker: str, period_days: int) -> pd.DataFrame | None:
    """近 N 日日線（含 Close/High/Low）。yfinance 被限流時退 FinMind。"""
    try:
        hist = yf.Ticker(ticker).history(
            period=f"{period_days}d", interval="1d", auto_adjust=False
        )
        if hist is not None and len(hist["Close"].dropna()) >= 2:
            return hist
        raise ValueError("yfinance 回傳空資料")
    except Exception as exc:
        logger.warning("index fetch %s failed: %s（改用 FinMind）", ticker, exc)
    try:
        df = finmind_us.fetch_daily(ticker)
        if df.empty:
            return None
        return df.tail(period_days).set_index("Date")
    except Exception as exc:
        logger.warning("finmind fallback %s failed: %s", ticker, exc)
        return None


def _fetch_quote(ticker: str, name: str) -> IndexQuote | None:
    hist = _history(ticker, 10)
    if hist is None:
        return None
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return None
    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
    return IndexQuote(name=name, close=round(last, 2), change_pct=round((last - prev) / prev * 100, 2))


def _fetch_local_levels(ticker: str) -> dict | None:
    """本地大盤近 90 日：現價、MA20/60、近 20 日高低（支撐壓力的技術依據）。"""
    hist = _history(ticker, 130)
    if hist is None:
        return None
    closes = hist["Close"].dropna()
    if len(closes) < 60:
        return None
    last = float(closes.iloc[-1])
    return {
        "close": round(last, 2),
        "change_pct": round((last - float(closes.iloc[-2])) / float(closes.iloc[-2]) * 100, 2),
        "ma20": round(float(closes.tail(20).mean()), 2),
        "ma60": round(float(closes.tail(60).mean()), 2),
        "high_20d": round(float(hist["High"].tail(20).max()), 2),
        "low_20d": round(float(hist["Low"].tail(20).min()), 2),
    }


TAIFEX_QUOTE_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"


def _taifex_quotes(market_type: str) -> list[dict]:
    """期交所 MIS 台指期報價（免費、免 key）。market_type: '0'=日盤、'1'=夜盤。"""
    payload = {
        "MarketType": market_type, "SymbolType": "F", "KindID": "1",
        "CID": "TXF", "ExpireMonth": "", "RowSize": "全部",
        "PageNo": "", "SortColumn": "", "AscDesc": "A",
    }
    res = httpx.post(
        TAIFEX_QUOTE_URL, json=payload, timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    res.raise_for_status()
    return (res.json().get("RtData") or {}).get("QuoteList") or []


def parse_night_futures(night_quotes: list[dict], day_quotes: list[dict]) -> dict | None:
    """近月台指期：夜盤最新價 vs 日盤收盤價 → 隔夜台股定價變化。

    SymbolID 尾碼 -M=夜盤、-F=日盤、-P/-S=現貨；近月＝清單中第一個有成交價的合約。
    """
    def first_traded(quotes: list[dict], suffix: str) -> dict | None:
        for q in quotes:
            symbol = q.get("SymbolID") or ""
            if symbol.startswith("TXF") and symbol.endswith(suffix) and q.get("CLastPrice"):
                return q
        return None

    night = first_traded(night_quotes, "-M")
    day = first_traded(day_quotes, "-F")
    if night is None or day is None:
        return None
    try:
        night_last = float(night["CLastPrice"])
        day_close = float(day["CLastPrice"])
    except (TypeError, ValueError):
        return None
    if day_close <= 0:
        return None
    return {
        "night_last": round(night_last, 0),
        "day_close": round(day_close, 0),
        "change_pct": round((night_last - day_close) / day_close * 100, 2),
        "contract": (night.get("SymbolID") or "").removesuffix("-M"),
    }


def _fetch_tw_night_futures() -> dict | None:
    try:
        return parse_night_futures(_taifex_quotes("1"), _taifex_quotes("0"))
    except Exception as exc:
        logger.warning("TAIFEX 夜盤報價失敗：%s", exc)
        return None


async def build_market_context(market: str) -> str:
    """組裝市場環境文字摘要（抓不到的項目誠實標示，不讓 AI 腦補）。"""

    def _gather() -> str:
        lines: list[str] = ["【全球指數（最近收盤 vs 前日）】"]
        for ticker, name in GLOBAL_TICKERS.items():
            q = _fetch_quote(ticker, name)
            lines.append(
                f"- {name}：{q.close:,}（{q.change_pct:+.2f}%）" if q else f"- {name}：資料暫缺"
            )
        adr = _fetch_quote(*ADR_TICKER)
        lines.append(
            f"- 台積電 ADR：{adr.close}（{adr.change_pct:+.2f}%）" if adr else "- 台積電 ADR：資料暫缺"
        )

        idx_ticker, idx_name = LOCAL_INDEX[market]
        levels = _fetch_local_levels(idx_ticker)
        lines.append(f"\n【{idx_name} 技術位階】")
        if levels:
            lines.append(
                f"- 收盤 {levels['close']:,}（{levels['change_pct']:+.2f}%）"
                f"｜MA20={levels['ma20']:,}、MA60={levels['ma60']:,}"
                f"｜近20日高低={levels['high_20d']:,}/{levels['low_20d']:,}"
            )
        else:
            lines.append("- 資料暫缺")

        if market == "TW":
            lines.append("\n【台指期夜盤（隔夜對台股的直接定價）】")
            night = _fetch_tw_night_futures()
            if night:
                lines.append(
                    f"- 近月 {night['contract']} 夜盤最新 {night['night_last']:,.0f}"
                    f"（較日盤收盤 {night['day_close']:,.0f} 變動 {night['change_pct']:+.2f}%）"
                )
            else:
                lines.append("- 資料暫缺")
        return "\n".join(lines)

    return await asyncio.to_thread(_gather)
