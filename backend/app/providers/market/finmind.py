"""台股資料源：FinMind API（主源）。

備援 TWSE OpenAPI 於 Phase 3（折溢價）加入。
"""
import logging
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
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    logger.warning("FinMind %s 第 %d 次失敗: %s", dataset, attempt, exc)
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
