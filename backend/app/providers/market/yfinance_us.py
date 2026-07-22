"""美股資料源：日線與搜尋皆以 FinMind 為主（官方 API），yfinance 備援。

Yahoo 對機房 IP 常限流，雲端上 yfinance 幾乎必失敗，故主從對調。
FinMind 免費層的美股僅有兩個資料集：USStockPrice（日線）與
USStockInfo（名稱/ETF 分類）——ETF 淨值與盤中報價它都沒有，
那兩處仍只能靠 yfinance（見 premium_service / intraday）。
yfinance 是同步庫，統一用 asyncio.to_thread 包成 async。
"""
import asyncio
import logging
from datetime import date, timedelta

import yfinance as yf

from app.core.exceptions import UpstreamError
from app.providers.market import finmind_us
from app.providers.market.base import MarketDataProvider, NavRow, OhlcvRow, StockInfo

logger = logging.getLogger(__name__)


class YFinanceProvider(MarketDataProvider):
    market = "US"

    async def search_stocks(self, query: str) -> list[StockInfo]:
        """美股不維護全清單：以 symbol 直接驗證（大寫代號查得到就回傳）。

        FinMind 的 USStockInfo 已提供名稱與 ETF 分類，故與日線一致以
        FinMind 為主源；Yahoo 對機房 IP 幾乎必限流，僅在 FinMind 查無時
        才退 yfinance（涵蓋 FinMind 未收錄的冷門代號）。
        """
        symbol = query.upper()
        info = await self._lookup_via_finmind(symbol)
        if info is not None:
            return [info]
        try:
            fallback = await self._lookup(symbol)
        except UpstreamError:
            # 兩邊都不可用：如實回報限流，不可誤判成「查無此代號」
            raise
        return [fallback] if fallback else []

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
            logger.warning("FinMind 查詢 %s 失敗：%s", symbol, exc)
            return None

    async def _lookup(self, symbol: str) -> StockInfo | None:
        from yfinance.exceptions import YFRateLimitError

        def _get() -> StockInfo | None:
            try:
                t = yf.Ticker(symbol)
                info = t.info
                if not info or info.get("regularMarketPrice") is None:
                    return None
                quote_type = (info.get("quoteType") or "").upper()
                return StockInfo(
                    symbol=symbol,
                    name=info.get("shortName") or symbol,
                    currency=info.get("currency") or "USD",
                    kind="etf" if quote_type == "ETF" else "stock",
                )
            except YFRateLimitError as exc:
                # 限流≠查無：吞掉會讓前端顯示「查無 INTC」誤導使用者
                logger.warning("yfinance lookup %s rate limited: %s", symbol, exc)
                raise UpstreamError("美股查詢暫時被上游限流，請稍後再試") from exc
            except Exception as exc:  # 其他 yfinance 例外型別不穩定，一律視為查無
                logger.warning("yfinance lookup %s failed: %s", symbol, exc)
                return None

        return await asyncio.to_thread(_get)

    async def get_daily_prices(self, symbol: str, start: date, end: date) -> list[OhlcvRow]:
        def _download() -> list[OhlcvRow]:
            df = yf.Ticker(symbol).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),  # yfinance end 為排除端點
                interval="1d",
                auto_adjust=False,
            )
            if df is None or df.empty:
                return []
            rows = []
            for idx, r in df.iterrows():
                rows.append(
                    OhlcvRow(
                        date=idx.date(),
                        open=float(r["Open"]),
                        high=float(r["High"]),
                        low=float(r["Low"]),
                        close=float(r["Close"]),
                        volume=int(r["Volume"]),
                    )
                )
            return rows

        def _download_finmind() -> list[OhlcvRow]:
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
            rows = await asyncio.to_thread(_download_finmind)
            if rows:
                return rows
            logger.warning("FinMind %s 查無日線，改試 yfinance", symbol)
        except Exception as exc:
            logger.warning("FinMind 抓取 %s 失敗（%s），改試 yfinance", symbol, exc)
        try:
            return await asyncio.to_thread(_download)
        except Exception as fallback_exc:
            raise UpstreamError(f"FinMind 與 yfinance 抓取 {symbol} 皆失敗") from fallback_exc

    async def get_etf_nav(self, symbol: str, start: date, end: date) -> list[NavRow]:
        return []  # Phase 3 實作

    async def get_institutional_flows(self, symbol: str, start: date, end: date) -> list[dict]:
        return []  # 美股無三大法人資料
