"""MarketDataProvider — 市場資料源抽象介面。

所有市場差異（交易時間、法人資料有無、NAV 來源）封裝在各實作內；
上層 service 一律透過此介面存取，不得直接呼叫外部 API。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class OhlcvRow:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


@dataclass(frozen=True)
class NavRow:
    date: date
    nav: float | None
    close: float | None


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    name: str
    currency: str
    kind: str  # 'stock' | 'etf'


class MarketDataProvider(ABC):
    """一個市場一組實作（TW: FinMind/TWSE、US: yfinance/Stooq）。"""

    market: str  # 'TW' | 'US'

    @abstractmethod
    async def search_stocks(self, query: str) -> list[StockInfo]: ...

    @abstractmethod
    async def get_daily_prices(
        self, symbol: str, start: date, end: date
    ) -> list[OhlcvRow]: ...

    @abstractmethod
    async def get_etf_nav(self, symbol: str, start: date, end: date) -> list[NavRow]:
        """無 NAV 資料的標的回傳空列表（前端顯示「不適用」）。"""
        ...

    @abstractmethod
    async def get_institutional_flows(self, symbol: str, start: date, end: date) -> list[dict]:
        """三大法人買賣超；美股無此資料，實作回傳空列表。"""
        ...
