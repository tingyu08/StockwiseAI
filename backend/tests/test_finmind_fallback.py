"""FinMind 美股/指數備援：回應解析、yfinance 限流時的 fallback 路徑。"""
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.providers.market import finmind_us
from app.providers.market import finmind

US_BODY = {
    "msg": "success", "status": 200,
    "data": [
        {"date": "2026-07-08", "stock_id": "SPY", "Open": 745.0, "High": 749.4,
         "Low": 742.4, "Close": 745.8, "Volume": 47100900, "Adj_Close": 745.8},
        {"date": "2026-07-09", "stock_id": "SPY", "Open": 747.4, "High": 751.3,
         "Low": 740.0, "Close": 744.8, "Volume": 57447800, "Adj_Close": 744.8},
    ],
}
TW_BODY = {
    "msg": "success", "status": 200,
    "data": [
        {"date": "2026-07-08", "stock_id": "TAIEX", "open": 46234.7, "max": 47293.1,
         "min": 46234.7, "close": 47018.99, "Trading_Volume": 14683404939},
        {"date": "2026-07-09", "stock_id": "TAIEX", "open": 47020.0, "max": 47500.0,
         "min": 46900.0, "close": 47394.5, "Trading_Volume": 11000000000},
    ],
}


def _mock_get(body):
    return lambda *a, **kw: SimpleNamespace(status_code=200, json=lambda: body)


def test_us_dataset_parses(monkeypatch):
    monkeypatch.setattr(finmind_us.httpx, "get", _mock_get(US_BODY))
    df = finmind_us.fetch_daily("SPY")
    assert len(df) == 2
    assert float(df["Close"].iloc[-1]) == 744.8


def test_twii_maps_to_taiex_fields(monkeypatch):
    captured = {}

    def fake_get(url, params=None, **kw):
        captured.update(params)
        return SimpleNamespace(status_code=200, json=lambda: TW_BODY)

    monkeypatch.setattr(finmind_us.httpx, "get", fake_get)
    df = finmind_us.fetch_daily("^TWII")
    assert captured["dataset"] == "TaiwanStockPrice"
    assert captured["data_id"] == "TAIEX"
    assert float(df["Close"].iloc[-1]) == 47394.5
    assert float(df["High"].iloc[-1]) == 47500.0  # max → High


def test_error_body_returns_empty(monkeypatch):
    monkeypatch.setattr(
        finmind_us.httpx, "get", _mock_get({"msg": "quota", "status": 402, "data": []})
    )
    assert finmind_us.fetch_daily("SPY").empty


def test_market_context_falls_back(monkeypatch):
    """yfinance 限流 → _history 改走 FinMind。"""
    from app.services import market_context

    class _RateLimited:
        def __init__(self, *a):
            pass

        def history(self, **kw):
            raise RuntimeError("Too Many Requests. Rate limited.")

    monkeypatch.setattr(market_context.yf, "Ticker", _RateLimited)
    monkeypatch.setattr(finmind_us.httpx, "get", _mock_get(US_BODY))
    quote = market_context._fetch_quote("^GSPC", "S&P 500")
    assert quote is not None
    assert quote.close == 744.8


async def test_us_provider_falls_back(monkeypatch):
    from app.providers.market.yfinance_us import YFinanceProvider, yf

    class _RateLimited:
        def __init__(self, *a):
            pass

        def history(self, **kw):
            raise RuntimeError("Too Many Requests. Rate limited.")

    monkeypatch.setattr(yf, "Ticker", _RateLimited)
    monkeypatch.setattr(finmind_us.httpx, "get", _mock_get(US_BODY))
    rows = await YFinanceProvider().get_daily_prices("SPY", date(2026, 7, 1), date(2026, 7, 9))
    assert len(rows) == 2
    assert rows[-1].close == 744.8


async def test_us_provider_falls_back_when_yfinance_returns_empty(monkeypatch):
    from app.providers.market.yfinance_us import YFinanceProvider, yf

    class _Empty:
        def __init__(self, *a):
            pass

        def history(self, **kw):
            return pd.DataFrame()

    fallback = pd.DataFrame({
        "Date": pd.to_datetime(["2026-07-09"]),
        "Open": [747.4], "High": [751.3], "Low": [740.0],
        "Close": [744.8], "Volume": [57447800],
    })
    monkeypatch.setattr(yf, "Ticker", _Empty)
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: fallback)

    rows = await YFinanceProvider().get_daily_prices(
        "SPY", date(2026, 7, 1), date(2026, 7, 9)
    )

    assert len(rows) == 1
    assert rows[0].close == 744.8


async def test_us_provider_prefers_finmind(monkeypatch):
    """日線主源為 FinMind：成功時完全不呼叫 yfinance。"""
    from app.providers.market.yfinance_us import YFinanceProvider, yf

    class _MustNotBeCalled:
        def __init__(self, *a):
            raise AssertionError("FinMind 成功時不應呼叫 yfinance")

    monkeypatch.setattr(yf, "Ticker", _MustNotBeCalled)
    monkeypatch.setattr(finmind_us.httpx, "get", _mock_get(US_BODY))
    rows = await YFinanceProvider().get_daily_prices("SPY", date(2026, 7, 1), date(2026, 7, 9))
    assert len(rows) == 2


def test_market_context_prefers_finmind(monkeypatch):
    from app.services import market_context

    class _MustNotBeCalled:
        def __init__(self, *a):
            raise AssertionError("FinMind 成功時不應呼叫 yfinance")

    monkeypatch.setattr(market_context.yf, "Ticker", _MustNotBeCalled)
    monkeypatch.setattr(finmind_us.httpx, "get", _mock_get(US_BODY))
    quote = market_context._fetch_quote("^GSPC", "S&P 500")
    assert quote is not None and quote.close == 744.8


# ---- 搜尋驗證的 FinMind 備援（Yahoo 限流時仍能新增自選股）----

def _patch_lookup_rate_limited(monkeypatch):
    from app.core.exceptions import UpstreamError
    from app.providers.market import yfinance_us

    async def rate_limited(self, symbol):
        raise UpstreamError("美股查詢暫時被上游限流，請稍後再試")

    monkeypatch.setattr(yfinance_us.YFinanceProvider, "_lookup", rate_limited)


async def test_search_falls_back_to_finmind_when_rate_limited(monkeypatch):
    from app.providers.market.yfinance_us import YFinanceProvider

    _patch_lookup_rate_limited(monkeypatch)
    fallback = pd.DataFrame({
        "Date": pd.to_datetime(["2026-07-20"]),
        "Open": [97.6], "High": [101.0], "Low": [96.9],
        "Close": [97.06], "Volume": [89273530],
    })
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: fallback)

    results = await YFinanceProvider().search_stocks("intc")

    assert len(results) == 1
    assert results[0].symbol == "INTC" and results[0].kind == "stock"


async def test_search_reraises_rate_limit_when_finmind_also_empty(monkeypatch):
    from app.core.exceptions import UpstreamError
    from app.providers.market.yfinance_us import YFinanceProvider

    _patch_lookup_rate_limited(monkeypatch)
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: pd.DataFrame())

    # FinMind 也查無 → 應如實回報「限流」而非誤判成「查無」
    with pytest.raises(UpstreamError, match="限流"):
        await YFinanceProvider().search_stocks("ZZZZZZ")


async def test_finmind_provider_retries_status_errors(monkeypatch):
    calls = 0

    class Response:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return Response(503, {"status": 503, "msg": "busy"})
            return Response(200, {"status": 200, "data": [{"stock_id": "2330"}]})

    delays = []

    async def no_wait(seconds):
        delays.append(seconds)

    monkeypatch.setattr(finmind.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(finmind, "sleep", no_wait, raising=False)

    rows = await finmind.FinMindProvider()._fetch("TaiwanStockInfo")

    assert rows == [{"stock_id": "2330"}]
    assert calls == 2
    assert delays == [pytest.approx(0.5)]
