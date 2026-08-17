"""夜盤取不到時必須說得出原因。

每日簡報連續多天出現「台指期夜盤資料暫缺」，但 log 一片乾淨：
2026-08-14 06:55 兩次 getQuoteList 都是 200 OK，也沒有任何 WARNING。
原因是 _fetch_tw_night_futures 只在「拋例外」時記 log，
parse_night_futures 回 None 這條路徑完全靜默——抓得到資料卻解析不出
近月合約時，外界無從得知。

診斷 log 上線後隔日即找到真因：夜盤有價、日盤全空——簡報排在 06:55，
而台股日盤 09:00 才開盤。基準價已改用夜盤自身的 CRefPrice，不再查日盤。
"""
import httpx
import pytest

from app.services import market_context

NIGHT_OK = [{"SymbolID": "TXFH6-M", "CLastPrice": "46389.00", "CRefPrice": "46170.00"}]


def _patch_quotes(monkeypatch, night):
    monkeypatch.setattr(market_context, "_taifex_quotes", lambda market_type: night)


def test_successful_fetch_logs_nothing(monkeypatch, caplog):
    _patch_quotes(monkeypatch, NIGHT_OK)

    with caplog.at_level("WARNING", logger="app.services.market_context"):
        result = market_context._fetch_tw_night_futures()

    assert result is not None and result["night_last"] == 46389
    assert caplog.text == "", "正常取得不該產生警告噪音"


@pytest.mark.parametrize(
    "night",
    [
        [{"SymbolID": "TXFH6-M", "CLastPrice": ""}],           # 無成交價
        [{"SymbolID": "TXFH6-M", "CLastPrice": "46389.00"}],   # 缺 CRefPrice
        [],                                                     # 整份空的
    ],
)
def test_unparsable_response_is_logged_with_evidence(monkeypatch, caplog, night):
    """解析不出來時要記下實際看到的東西，否則無從判斷是收盤空窗還是格式變更。"""
    _patch_quotes(monkeypatch, night)

    with caplog.at_level("WARNING", logger="app.services.market_context"):
        assert market_context._fetch_tw_night_futures() is None

    assert caplog.text, "解析失敗卻完全沒有 log——這正是查不出原因的癥結"
    assert "夜盤" in caplog.text


def test_upstream_error_names_the_exception_type(monkeypatch, caplog):
    """逾時例外的 str() 是空字串，只印 exc 會得到一句沒有內容的話。"""
    def boom(market_type: str):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(market_context, "_taifex_quotes", boom)

    with caplog.at_level("WARNING", logger="app.services.market_context"):
        assert market_context._fetch_tw_night_futures() is None

    assert "ReadTimeout" in caplog.text


def test_digest_is_bounded(monkeypatch, caplog):
    """診斷訊息不可把整份報價倒進 log。"""
    _patch_quotes(
        monkeypatch,
        [{"SymbolID": f"TXF{i}-M", "CLastPrice": ""} for i in range(50)],
    )

    with caplog.at_level("WARNING", logger="app.services.market_context"):
        market_context._fetch_tw_night_futures()

    assert len(caplog.text) < 600, f"log 太長（{len(caplog.text)} 字元）"
