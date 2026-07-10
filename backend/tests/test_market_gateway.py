from datetime import date
import importlib


async def test_market_gateway_is_the_single_provider_entrypoint():
    calls = []

    class FakeProvider:
        async def get_daily_prices(self, symbol, start, end):
            calls.append(("prices", symbol, start, end))
            return [symbol]

        async def search_stocks(self, query):
            calls.append(("search", query))
            return [query]

        async def get_institutional_flows(self, symbol, start, end):
            calls.append(("flows", symbol, start, end))
            return []

    try:
        market_gateway = importlib.import_module("app.services.market_gateway")
    except ModuleNotFoundError:
        market_gateway = None
    gateway_type = getattr(market_gateway, "MarketDataGateway", None)
    assert gateway_type is not None
    if gateway_type is None:
        return
    gateway = gateway_type(lambda market: FakeProvider())
    start, end = date(2026, 7, 1), date(2026, 7, 10)

    assert await gateway.get_daily_prices("TW", "2330", start, end) == ["2330"]
    assert await gateway.search_stocks("TW", "台積") == ["台積"]
    assert await gateway.get_institutional_flows("TW", "2330", start, end) == []
    assert [call[0] for call in calls] == ["prices", "search", "flows"]
