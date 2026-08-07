"""美股資料源：日線、搜尋與名稱分類皆走 FinMind 官方 API。

曾以 yfinance 為備援，已完整移除：Yahoo 以 IP 信譽封鎖機房來源，正式環境上
它是「必定失敗的備援」而非「偶爾失敗的備援」，只會讓失敗看起來像有在努力，
並白花請求與延遲。移除前逐一實測 FinMind 涵蓋所有實際用到的代號
（指數、大型股、小型股、ETF 含 kind 分類），無功能損失。

FinMind 免費層的美股有兩個資料集：USStockPrice（日線）與
USStockInfo（名稱/ETF 分類）。盤中即時報價由 Finnhub 提供（見 intraday）；
ETF 淨值兩者皆無，故美股不支援折溢價（見 premium_service.SUPPORTED_MARKETS）。
FinMind 是同步庫，統一用 asyncio.to_thread 包成 async。
"""
import asyncio
import logging
from datetime import date

from app.core.exceptions import UpstreamError
from app.providers.market import finmind_us
from app.providers.market.base import MarketDataProvider, NavRow, OhlcvRow, StockInfo

logger = logging.getLogger(__name__)


class USMarketProvider(MarketDataProvider):
    market = "US"

    async def search_stocks(self, query: str) -> list[StockInfo]:
        """美股不維護全清單：以 symbol 直接驗證（大寫代號查得到就回傳）。"""
        symbol = query.upper()
        info = await self._lookup_via_finmind(symbol)
        return [info] if info is not None else []

    @staticmethod
    async def _lookup_via_finmind(symbol: str) -> StockInfo | None:
        """近幾日有日線＝代號存在；名稱與 ETF 分類取自 USStockInfo。"""

        def _get() -> StockInfo | None:
            if finmind_us.fetch_daily(symbol).empty:
                return None
            meta = finmind_us.fetch_stock_info(symbol) or {}
            return StockInfo(
                symbol=symbol,
                name=meta.get("name") or symbol,
                currency="USD",
                kind=meta.get("kind") or "stock",
            )

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            # 上游故障不可偽裝成「查無此代號」——那會讓使用者以為代號打錯
            logger.warning("FinMind 查詢 %s 失敗：%s", symbol, exc)
            raise UpstreamError("美股查詢暫時無法使用，請稍後再試") from exc

    async def get_daily_prices(self, symbol: str, start: date, end: date) -> list[OhlcvRow]:
        def _download() -> list[OhlcvRow]:
            df = finmind_us.fetch_daily(symbol, start=start, end=end)
            return [
                OhlcvRow(
                    date=r["Date"].date(),
                    open=float(r["Open"]),
                    high=float(r["High"]),
                    low=float(r["Low"]),
                    close=float(r["Close"]),
                    volume=int(r["Volume"]),
                )
                for _, r in df.iterrows()
            ]

        try:
            return await asyncio.to_thread(_download)
        except Exception as exc:
            raise UpstreamError(f"FinMind 抓取 {symbol} 日線失敗") from exc

    async def get_etf_nav(self, symbol: str, start: date, end: date) -> list[NavRow]:
        return []  # FinMind 美股無淨值資料集（premium_service 亦不支援美股）

    async def get_institutional_flows(self, symbol: str, start: date, end: date) -> list[dict]:
        return []  # 美股無三大法人資料
