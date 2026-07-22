"""台股資料源：FinMind API（主源）。

備援 TWSE OpenAPI 於 Phase 3（折溢價）加入。
"""
import logging
from asyncio import sleep
from datetime import date

import httpx

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.providers.market.base import MarketDataProvider, NavRow, OhlcvRow, StockInfo

logger = logging.getLogger(__name__)

API_URL = "https://api.finmindtrade.com/api/v4/data"
RETRIES = 3


class FinMindProvider(MarketDataProvider):
    market = "TW"

    async def _fetch(self, dataset: str, **params) -> list[dict]:
        settings = get_settings()
        query = {"dataset": dataset, "token": settings.finmind_token, **params}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(1, RETRIES + 1):
                try:
                    res = await client.get(API_URL, params=query)
                    body = res.json()
                    if res.status_code != 200 or body.get("status") != 200:
                        raise UpstreamError(
                            f"FinMind {dataset} 回應異常：{body.get('msg', res.status_code)}"
                        )
                    return body.get("data", [])
                except (httpx.HTTPError, ValueError, UpstreamError) as exc:
                    last_error = exc
                    logger.warning("FinMind %s 第 %d 次失敗: %s", dataset, attempt, exc)
                    if attempt < RETRIES:
                        await sleep(0.5 * (2 ** (attempt - 1)))
        raise UpstreamError(f"FinMind {dataset} 連續 {RETRIES} 次失敗") from last_error

    async def search_stocks(self, query: str) -> list[StockInfo]:
        rows = await self._fetch("TaiwanStockInfo")
        seen: set[str] = set()
        results = []
        for r in rows:
            sid = r["stock_id"]
            if sid in seen:
                continue
            if query not in sid and query not in r["stock_name"]:
                continue
            seen.add(sid)
            is_etf = r.get("industry_category") == "ETF"
            results.append(
                StockInfo(
                    symbol=sid,
                    name=r["stock_name"],
                    currency="TWD",
                    kind="etf" if is_etf else "stock",
                )
            )
        return results[:50]

    async def get_daily_prices(self, symbol: str, start: date, end: date) -> list[OhlcvRow]:
        rows = await self._fetch(
            "TaiwanStockPrice",
            data_id=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        return [
            OhlcvRow(
                date=date.fromisoformat(r["date"]),
                open=r.get("open"),
                high=r.get("max"),
                low=r.get("min"),
                close=r.get("close"),
                volume=r.get("Trading_Volume"),
            )
            for r in rows
        ]

    async def get_etf_nav(self, symbol: str, start: date, end: date) -> list[NavRow]:
        return []  # Phase 3 實作（TWSE 淨值源）

    async def get_institutional_flows(self, symbol: str, start: date, end: date) -> list[dict]:
        return await self._fetch(
            "TaiwanStockInstitutionalInvestorsBuySell",
            data_id=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    # ---- 台股專屬資料集（免費層可用；美股 FinMind 無對應資料）----

    async def get_valuation(self, symbol: str, start: date, end: date) -> list[dict]:
        """每日本益比／股價淨值比／現金殖利率（TaiwanStockPER）。"""
        return await self._fetch(
            "TaiwanStockPER",
            data_id=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    async def get_margin_trading(self, symbol: str, start: date, end: date) -> list[dict]:
        """融資融券餘額（TaiwanStockMarginPurchaseShortSale），單位為張。"""
        return await self._fetch(
            "TaiwanStockMarginPurchaseShortSale",
            data_id=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    async def get_monthly_revenue(self, symbol: str, start: date, end: date) -> list[dict]:
        """月營收（TaiwanStockMonthRevenue）。需涵蓋去年同月才算得出年增率。"""
        return await self._fetch(
            "TaiwanStockMonthRevenue",
            data_id=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

    async def _by_symbol(self, dataset: str, symbol: str, start: date) -> list[dict]:
        return await self._fetch(dataset, data_id=symbol, start_date=start.isoformat())

    async def get_short_sale_balances(self, symbol: str, start: date) -> list[dict]:
        """信用額度總量管制餘額：含借券賣出（SBL）餘額，比券資比更完整的空方壓力。"""
        return await self._by_symbol("TaiwanDailyShortSaleBalances", symbol, start)

    async def get_short_sale_suspension(self, symbol: str, start: date) -> list[dict]:
        """暫停融券期間與原因（除權息、軋空管制等）。"""
        return await self._by_symbol("TaiwanStockMarginShortSaleSuspension", symbol, start)

    async def get_income_statement(self, symbol: str, start: date) -> list[dict]:
        """綜合損益表（季）：Revenue／GrossProfit／OperatingIncome／IncomeAfterTaxes／EPS。"""
        return await self._by_symbol("TaiwanStockFinancialStatements", symbol, start)

    async def get_balance_sheet(self, symbol: str, start: date) -> list[dict]:
        """資產負債表（季）：TotalAssets／Liabilities／CurrentAssets／CurrentLiabilities 等。"""
        return await self._by_symbol("TaiwanStockBalanceSheet", symbol, start)

    async def get_cash_flows(self, symbol: str, start: date) -> list[dict]:
        """現金流量表（季）：NetCashInflowFromOperatingActivities 等。"""
        return await self._by_symbol("TaiwanStockCashFlowsStatement", symbol, start)

    # ---- 全市場資料（不帶 data_id，全體共用一次請求）----

    async def get_active_etf_list(self) -> list[dict]:
        """主動式 ETF 清單——主動式與被動指數 ETF 的分析邏輯不同，需要區分。"""
        return await self._fetch("TaiwanStockActiveETFInfo")

    async def get_futures_institutional_investors(self, start: date) -> list[dict]:
        """台指期（TX）三大法人未平倉——外資淨部位是大盤方向最前瞻的籌碼指標。"""
        return await self._fetch(
            "TaiwanFuturesInstitutionalInvestors",
            data_id="TX",
            start_date=start.isoformat(),
        )
