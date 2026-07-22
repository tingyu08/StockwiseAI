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


_PRICED = pd.DataFrame({
    "Date": pd.to_datetime(["2026-07-20"]),
    "Open": [97.6], "High": [101.0], "Low": [96.9],
    "Close": [97.06], "Volume": [89273530],
})


async def test_search_uses_finmind_metadata_for_stock(monkeypatch):
    """FinMind 為主源：名稱取自 USStockInfo，不必動用 yfinance。"""
    from app.providers.market.yfinance_us import YFinanceProvider

    _patch_lookup_rate_limited(monkeypatch)  # yfinance 一旦被呼叫就會炸
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: _PRICED)
    monkeypatch.setattr(
        finmind_us, "fetch_stock_info",
        lambda s: {"name": "Intel Corporation Common Stock", "kind": "stock"},
    )

    results = await YFinanceProvider().search_stocks("intc")

    assert len(results) == 1
    assert results[0].symbol == "INTC"
    assert results[0].name == "Intel Corporation Common Stock"
    assert results[0].kind == "stock"


async def test_search_classifies_etf_from_finmind_subsector(monkeypatch):
    """ETF 必須被正確分類，否則不會被納入 NAV/折溢價追蹤。"""
    from app.providers.market.yfinance_us import YFinanceProvider

    _patch_lookup_rate_limited(monkeypatch)
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: _PRICED)
    monkeypatch.setattr(
        finmind_us, "fetch_stock_info",
        lambda s: {"name": "Invesco QQQ Trust Series 1", "kind": "etf"},
    )

    results = await YFinanceProvider().search_stocks("qqq")

    assert results[0].kind == "etf"
    assert results[0].name == "Invesco QQQ Trust Series 1"


async def test_search_still_returns_symbol_when_metadata_missing(monkeypatch):
    """有日線但 USStockInfo 查無 → 仍可加入自選，名稱退回代號。"""
    from app.providers.market.yfinance_us import YFinanceProvider

    _patch_lookup_rate_limited(monkeypatch)
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: _PRICED)
    monkeypatch.setattr(finmind_us, "fetch_stock_info", lambda s: None)

    results = await YFinanceProvider().search_stocks("intc")

    assert results[0].name == "INTC" and results[0].kind == "stock"


async def test_search_reraises_rate_limit_when_finmind_also_empty(monkeypatch):
    from app.core.exceptions import UpstreamError
    from app.providers.market.yfinance_us import YFinanceProvider

    _patch_lookup_rate_limited(monkeypatch)
    monkeypatch.setattr(finmind_us, "fetch_daily", lambda *a, **kw: pd.DataFrame())

    # FinMind 查無且 yfinance 被限流 → 如實回報「限流」而非誤判成「查無」
    with pytest.raises(UpstreamError, match="限流"):
        await YFinanceProvider().search_stocks("ZZZZZZ")


def test_fetch_stock_info_maps_etf_subsector(monkeypatch):
    body = {"msg": "success", "status": 200, "data": [
        {"date": "2026-07-22", "stock_id": "QQQ", "Subsector": "ETF",
         "stock_name": "Invesco QQQ Trust Series 1"},
    ]}
    monkeypatch.setattr(finmind_us.httpx, "get", _mock_get(body))
    assert finmind_us.fetch_stock_info("QQQ") == {
        "name": "Invesco QQQ Trust Series 1", "kind": "etf",
    }


def test_fetch_stock_info_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(
        finmind_us.httpx, "get", _mock_get({"status": 200, "data": []})
    )
    assert finmind_us.fetch_stock_info("ZZZZ") is None


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


# ---- 效能：避免重複下載 ----

async def test_stock_catalog_is_cached(monkeypatch):
    """TaiwanStockInfo 是數 MB 全市場目錄：搜尋與新增自選股不該每次重抓。"""
    from app.providers.market import finmind as finmind_mod

    finmind_mod._catalog = None  # 清掉其他測試可能留下的快取
    calls = {"n": 0}

    async def counting_fetch(self, dataset, **params):
        calls["n"] += 1
        return [
            {"stock_id": "2330", "stock_name": "台積電", "industry_category": "半導體業"}
        ]

    monkeypatch.setattr(finmind_mod.FinMindProvider, "_fetch", counting_fetch)
    provider = finmind_mod.FinMindProvider()
    try:
        first = await provider.search_stocks("2330")
        second = await provider.search_stocks("台積")
        assert first[0].symbol == "2330" and second[0].symbol == "2330"
        assert calls["n"] == 1, f"目錄被下載了 {calls['n']} 次，TTL 快取沒生效"
    finally:
        finmind_mod._catalog = None


async def test_us_market_context_fetches_gspc_once(monkeypatch):
    """^GSPC 同時列在全球指數與美股本地大盤，一次簡報只該抓一趟。"""
    from app.services import market_context

    dates = pd.date_range("2025-09-01", periods=200, freq="D")
    frame = pd.DataFrame({
        "Date": dates,
        "Open": [100.0 + i for i in range(200)],
        "High": [101.0 + i for i in range(200)],
        "Low": [99.0 + i for i in range(200)],
        "Close": [100.0 + i for i in range(200)],
        "Volume": [1000.0] * 200,
    })
    calls: list[str] = []

    def counting_fetch(symbol, start=None, end=None):
        calls.append(symbol)
        return frame.copy()

    monkeypatch.setattr(market_context.finmind_us, "fetch_daily", counting_fetch)

    text = await market_context.build_market_context("US")

    assert "S&P 500" in text
    assert calls.count("^GSPC") == 1, f"^GSPC 被抓了 {calls.count('^GSPC')} 次"
