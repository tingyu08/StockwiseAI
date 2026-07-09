"""市場環境資料：全球指數、台積電 ADR、加權指數技術位階（yfinance，免費）。

供每日簡報的「全球盤勢」與「大盤預判」模組使用——後端抓真實數據餵 AI，
AI 只負責解讀，不虛構行情。
"""
import asyncio
import logging
from dataclasses import dataclass

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
        return "\n".join(lines)

    return await asyncio.to_thread(_gather)
