"""美股資料源：日線以 FinMind 為主（官方 API），yfinance 備援。

Yahoo 對機房 IP 常限流，雲端上 yfinance 幾乎必失敗，故日線主從對調；
搜尋驗證仍以 yfinance 優先（需要名稱/ETF 類型中繼資料，FinMind 沒有）。
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

        yfinance 被 Yahoo 限流時退 FinMind 驗證：近幾日有日線＝代號存在。
        FinMind 沒有名稱/類型資訊，name 先用代號、kind 猜 stock——
        之後 yfinance 解封、資料同步時不受影響（同步只認 symbol）。
        """
        symbol = query.upper()
        try:
            info = await self._lookup(symbol)
        except UpstreamError:
            info = await self._lookup_via_finmind(symbol)
            if info is None:
                raise  # FinMind 也查無 → 如實回報限流，而非誤判成「查無」
        return [info] if info else []

    @staticmethod
    async def _lookup_via_finmind(symbol: str) -> StockInfo | None:
        def _get() -> StockInfo | None:
            df = finmind_us.fetch_daily(symbol)
            if df.empty:
                return None
            return StockInfo(symbol=symbol, name=symbol, currency="USD", kind="stock")

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            logger.warning("FinMind 備援查詢 %s 失敗：%s", symbol, exc)
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
