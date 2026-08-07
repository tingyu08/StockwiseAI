"""美股盤中報價：Finnhub 為唯一來源。

Yahoo（yfinance）以 IP 信譽封鎖機房來源，先是被降為備援，最終完整移除——
它在正式環境必定 429，留著只是讓失敗看起來像有在努力。Finnhub 以 API key
辨識呼叫者，不看 IP，這是它取而代之的理由。
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


def _patch_client(monkeypatch, by_symbol, *, capture=None):
    """by_symbol: symbol -> _Resp／例外實例／上述之 list（依序回應，用於重試）。

    capture 傳入 dict 時，會記下 AsyncClient 的建構參數與每次 get 的 params，
    供「token 不得出現在 URL」這類契約斷言使用。
    """
    requested = []
    sequences = {
        symbol: list(outcome) if isinstance(outcome, list) else None
        for symbol, outcome in by_symbol.items()
    }

    class Client:
        def __init__(self, kwargs):
            if capture is not None:
                capture["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            symbol = (params or {}).get("symbol")
            requested.append(symbol)
            if capture is not None:
                capture.setdefault("params", []).append(params or {})
            queue = sequences.get(symbol)
            if queue:
                outcome = queue.pop(0)
            else:
                outcome = by_symbol.get(symbol, _Resp(200, {"c": 0}))
                if isinstance(outcome, list):  # 序列已用盡 → 沿用最後一個結果
                    outcome = outcome[-1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(finnhub.httpx, "AsyncClient", lambda **kw: Client(kw))
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


# ---- 憑證不得外洩到 log ----


async def test_token_travels_in_header_never_in_the_url(monkeypatch):
    """token 走 header。

    query string 會被 httpx 的 INFO log、反向代理與平台 access log 原樣記下：
    2026-08-06 的正式環境 log 就有整串 token 明文（見 finnhubio/Finnhub-API#301）。
    """
    capture: dict = {}
    _patch_client(monkeypatch, {"AAPL": _Resp(200, {"c": 1.5})}, capture=capture)

    await finnhub.fetch_quotes(["AAPL"])

    headers = capture["client_kwargs"]["headers"]
    assert headers["X-Finnhub-Token"] == "test-finnhub-token"
    for params in capture["params"]:
        assert "token" not in params, f"token 仍在 query string：{params}"


def test_finnhub_token_is_redacted_from_logs():
    """防禦深度：萬一有其他路徑印出 token，遮蔽層要接得住。

    原本 _secrets() 漏了 finnhub_token——FinMind 的 token 在 log 裡是
    [REDACTED]，Finnhub 的卻是明文，正是這個遺漏造成的。
    """
    from app.core.logging_config import redact_sensitive

    settings = get_settings()
    token = "d9lbnlpr01qlqi02cn30"
    object.__setattr__(settings, "finnhub_token", token)
    try:
        message = f"GET https://finnhub.io/api/v1/quote?symbol=TSM&token={token}"
        assert token not in redact_sensitive(message, settings)
    finally:
        object.__setattr__(settings, "finnhub_token", "test-finnhub-token")


# ---- 暫時性故障的韌性與可診斷性 ----


async def test_timeout_is_retried_before_giving_up(monkeypatch):
    """逾時重試一次再放棄。

    這是唯一的美股報價來源，一次逾時就等於當輪停損完全失效——
    2026-08-06 18:40 的哨兵「5 檔持倉全滅」即如此。
    """
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(finnhub, "_sleep", no_sleep, raising=False)
    requested = _patch_client(
        monkeypatch,
        {"AAPL": [httpx.ReadTimeout(""), _Resp(200, {"c": 261.74})]},
    )

    assert await finnhub.fetch_quotes(["AAPL"]) == {"AAPL": 261.74}
    assert requested == ["AAPL", "AAPL"], "逾時後沒有重試"
    assert slept, "重試之前沒有退避"


async def test_repeated_timeout_gives_up_without_killing_the_batch(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(finnhub, "_sleep", no_sleep, raising=False)
    _patch_client(
        monkeypatch,
        {
            "AAPL": [httpx.ReadTimeout(""), httpx.ReadTimeout("")],
            "MSFT": _Resp(200, {"c": 502.1}),
        },
    )

    assert await finnhub.fetch_quotes(["AAPL", "MSFT"]) == {"MSFT": 502.1}


async def test_connect_error_is_not_retried(monkeypatch):
    """連線被拒是確定性失敗，重試只是拖慢哨兵——逾時才值得再試一次。"""
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(finnhub, "_sleep", no_sleep, raising=False)
    requested = _patch_client(monkeypatch, {"AAPL": httpx.ConnectError("refused")})

    assert await finnhub.fetch_quotes(["AAPL"]) == {}
    assert requested == ["AAPL"]


async def test_failure_log_names_the_exception_type(monkeypatch, caplog):
    """httpx 的逾時例外 str() 是空字串，只印 exc 會得到「連線失敗：」。

    正式環境的 log 正是如此——完全看不出是逾時、連線被拒還是 TLS 失敗。
    """
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(finnhub, "_sleep", no_sleep, raising=False)
    _patch_client(monkeypatch, {"AAPL": httpx.ReadTimeout("")})

    with caplog.at_level("WARNING", logger="app.providers.market.finnhub"):
        await finnhub.fetch_quotes(["AAPL"])

    assert "ReadTimeout" in caplog.text


# ---- 美股盤中報價的唯一來源 ----


async def test_finnhub_is_the_only_us_intraday_source(monkeypatch):
    """Finnhub 取不到的標的就是沒有——yfinance 備援已完整移除。

    Yahoo 對機房 IP 必定 429，留著它只是讓失敗看起來像有在努力，
    還白花請求與延遲（2026-08-06 18:40：5 檔全數退 yfinance、全數 429）。
    """
    _patch_client(monkeypatch, {"AAPL": _Resp(200, {"c": 261.74})})  # MSFT 取不到

    assert await intraday._us_quotes(["AAPL", "MSFT"]) == {"AAPL": 261.74}


async def test_no_token_means_no_us_quotes_at_all(monkeypatch):
    """未設定 token 時不再有替代來源：如實回空，讓哨兵那輪算失敗。"""
    monkeypatch.setattr(get_settings(), "finnhub_token", "")

    assert await intraday._us_quotes(["AAPL"]) == {}


async def test_tw_market_is_untouched_by_us_changes(monkeypatch):
    """台股走證交所官方端點，不該因為美股換來源而受影響。"""
    called = []

    async def fake_tw(symbols):
        called.append(symbols)
        return {"2330": 1000.0}

    monkeypatch.setattr(intraday, "_tw_quotes", fake_tw)
    assert await intraday.fetch_intraday_quotes("TW", ["2330"]) == {"2330": 1000.0}
    assert called == [["2330"]]
