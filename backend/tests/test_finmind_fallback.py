"""FinMind 美股/指數備援：回應解析、yfinance 限流時的 fallback 路徑。"""
from datetime import date
from types import SimpleNamespace

from app.providers.market import finmind_us

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
