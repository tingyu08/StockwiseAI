"""美股盤中報價改以 Finnhub 為主、yfinance 為備援。

起因：Yahoo 以 IP 信譽封鎖機房來源，正式環境從「偶爾一檔失敗」惡化到
「3 檔持倉全滅」，停損/停利實質完全失效。Finnhub 以 API key 辨識呼叫者，
不看 IP，這是主從順序反轉的理由。
"""
import httpx
import pytest

from app.core.config import get_settings
from app.providers.market import finnhub, intraday


class _Resp:
    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raise = raise_exc

    def json(self):
        if self._raise:
            raise self._raise
        return self._payload


def _patch_client(monkeypatch, by_symbol):
    """by_symbol: symbol -> _Resp 或例外實例。"""
    requested = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            symbol = (params or {}).get("symbol")
            requested.append(symbol)
            outcome = by_symbol.get(symbol, _Resp(200, {"c": 0}))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(finnhub.httpx, "AsyncClient", lambda **kw: Client())
    return requested


@pytest.fixture(autouse=True)
def _with_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "finnhub_token", "test-finnhub-token")
    monkeypatch.setattr(finnhub, "_missing_token_warned", False)


# ---- Finnhub provider ----

async def test_returns_current_price_field(monkeypatch):
    _patch_client(monkeypatch, {"AAPL": _Resp(200, {"c": 261.74, "pc": 260.0})})
    assert await finnhub.fetch_quotes(["AAPL"]) == {"AAPL": 261.74}


async def test_unknown_symbol_returns_zeros_and_must_be_dropped(monkeypatch):
    """Finnhub 對未知代號回整組 0。把 0 當成交價會直接誤觸發停損。"""
    _patch_client(monkeypatch, {"NOPE": _Resp(200, {"c": 0, "h": 0, "l": 0, "pc": 0})})
    assert await finnhub.fetch_quotes(["NOPE"]) == {}


async def test_rate_limited_symbol_is_dropped_not_raised(monkeypatch):
    _patch_client(monkeypatch, {
        "AAPL": _Resp(429),
        "MSFT": _Resp(200, {"c": 502.1}),
    })
    assert await finnhub.fetch_quotes(["AAPL", "MSFT"]) == {"MSFT": 502.1}


async def test_transport_error_does_not_kill_the_batch(monkeypatch):
    _patch_client(monkeypatch, {
        "AAPL": httpx.ConnectError("boom"),
        "MSFT": _Resp(200, {"c": 502.1}),
    })
    assert await finnhub.fetch_quotes(["AAPL", "MSFT"]) == {"MSFT": 502.1}


async def test_non_json_body_is_dropped(monkeypatch):
    _patch_client(monkeypatch, {"AAPL": _Resp(200, raise_exc=ValueError("not json"))})
    assert await finnhub.fetch_quotes(["AAPL"]) == {}


async def test_no_token_returns_empty_without_calling_upstream(monkeypatch):
    monkeypatch.setattr(get_settings(), "finnhub_token", "")
    requested = _patch_client(monkeypatch, {"AAPL": _Resp(200, {"c": 1.0})})
    assert await finnhub.fetch_quotes(["AAPL"]) == {}
    assert requested == []  # 沒 token 不該白打上游


async def test_empty_symbol_list_short_circuits(monkeypatch):
    requested = _patch_client(monkeypatch, {})
    assert await finnhub.fetch_quotes([]) == {}
    assert requested == []


# ---- 與 yfinance 的主從關係 ----

async def test_finnhub_is_primary_and_yfinance_not_touched(monkeypatch):
    """Finnhub 全數命中時不該再去打 Yahoo——那正是被限流的來源。"""
    _patch_client(monkeypatch, {
        "AAPL": _Resp(200, {"c": 261.74}),
        "MSFT": _Resp(200, {"c": 502.1}),
    })
    called = []

    async def fake_yf(symbols):
        called.append(symbols)
        return {}

    monkeypatch.setattr(intraday, "_us_quotes_via_yfinance", fake_yf)

    quotes = await intraday._us_quotes(["AAPL", "MSFT"])

    assert quotes == {"AAPL": 261.74, "MSFT": 502.1}
    assert called == []


async def test_yfinance_only_fills_the_gaps(monkeypatch):
    _patch_client(monkeypatch, {"AAPL": _Resp(200, {"c": 261.74})})  # MSFT 取不到
    called = []

    async def fake_yf(symbols):
        called.append(symbols)
        return {"MSFT": 500.0}

    monkeypatch.setattr(intraday, "_us_quotes_via_yfinance", fake_yf)

    quotes = await intraday._us_quotes(["AAPL", "MSFT"])

    assert quotes == {"AAPL": 261.74, "MSFT": 500.0}
    assert called == [["MSFT"]]  # 只補缺的那檔，不重打已取得的


async def test_falls_back_entirely_when_token_missing(monkeypatch):
    """未設定 token 時行為等同修改前，不會讓哨兵直接壞掉。"""
    monkeypatch.setattr(get_settings(), "finnhub_token", "")

    async def fake_yf(symbols):
        return {s: 100.0 for s in symbols}

    monkeypatch.setattr(intraday, "_us_quotes_via_yfinance", fake_yf)

    assert await intraday._us_quotes(["AAPL"]) == {"AAPL": 100.0}


async def test_tw_market_is_untouched_by_us_changes(monkeypatch):
    """台股走證交所官方端點，不該因為美股換來源而受影響。"""
    called = []

    async def fake_tw(symbols):
        called.append(symbols)
        return {"2330": 1000.0}

    monkeypatch.setattr(intraday, "_tw_quotes", fake_tw)
    assert await intraday.fetch_intraday_quotes("TW", ["2330"]) == {"2330": 1000.0}
    assert called == [["2330"]]
