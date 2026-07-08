from app.core.exceptions import NotFoundError
from app.providers.market.base import MarketDataProvider
from app.providers.market.finmind import FinMindProvider
from app.providers.market.yfinance_us import YFinanceProvider

_PROVIDERS: dict[str, MarketDataProvider] = {
    "TW": FinMindProvider(),
    "US": YFinanceProvider(),
}


def get_provider(market: str) -> MarketDataProvider:
    provider = _PROVIDERS.get(market.upper())
    if provider is None:
        raise NotFoundError(f"不支援的市場：{market}")
    return provider
