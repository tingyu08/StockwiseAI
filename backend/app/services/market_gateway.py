"""Single application entrypoint for market-data providers."""

from collections.abc import Callable
from datetime import date

from app.providers.market.base import MarketDataProvider
from app.providers.market.registry import get_provider


class MarketDataGateway:
    def __init__(
        self,
        provider_resolver: Callable[[str], MarketDataProvider] = get_provider,
    ) -> None:
        self._provider_resolver = provider_resolver

    def provider(self, market: str) -> MarketDataProvider:
        return self._provider_resolver(market)

    async def search_stocks(self, market: str, query: str):
        return await self.provider(market).search_stocks(query)

    async def get_daily_prices(
        self, market: str, symbol: str, start: date, end: date
    ):
        return await self.provider(market).get_daily_prices(symbol, start, end)

    async def get_institutional_flows(
        self, market: str, symbol: str, start: date, end: date
    ):
        return await self.provider(market).get_institutional_flows(symbol, start, end)


market_data = MarketDataGateway()
