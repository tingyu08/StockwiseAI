"""新聞來源：拿真實標題與網址，不靠「AI 自己上網」。

背景：Gemini 的 Google 搜尋接地在本專案方案下一律 429，Antigravity agent
建得起任務卻讀不到結果（403）——都是 Google 端的資格問題。實測純文字生成
與 url_context 正常，所以把「找新聞」與「摘要新聞」拆開。

拆開的附帶好處：出處是我們給的，AI 沒有機會虛構來源。
"""
import httpx
import pytest

from app.core.config import get_settings
from app.providers import news_feed


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


def _patch_client(monkeypatch, by_host):
    """by_host: 網址片段 -> _Resp 或例外。"""
    seen = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            seen.append(url)
            for fragment, outcome in by_host.items():
                if fragment in url:
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome
            return _Resp(404)

    monkeypatch.setattr(news_feed.httpx, "AsyncClient", lambda **kw: Client())
    return seen


FINMIND_OK = _Resp(200, {"data": [
    {"date": "2026-08-01 09:00:00", "title": "台積電法說會優於預期 - 經濟日報",
     "link": "https://money.example/a"},
    {"date": "2026-08-02 10:00:00", "title": "外資調高目標價 - 工商時報",
     "link": "https://ctee.example/b"},
]})

RSS_OK = _Resp(200, text="""<rss><channel>
<item><title>台股收紅 - 中央社</title><link>https://cna.example/x</link>
<pubDate>Mon, 03 Aug 2026 01:23:45 GMT</pubDate></item>
</channel></rss>""")


# ---- 台股：FinMind ----

async def test_tw_uses_finmind_and_splits_the_source_suffix(monkeypatch):
    """FinMind 的 title 是「標題 - 媒體」，要拆出來當來源。"""
    _patch_client(monkeypatch, {"finmindtrade.com": FINMIND_OK})

    items = await news_feed.fetch_headlines("2330", "台積電", "TW")

    assert [i.source for i in items] == ["工商時報", "經濟日報"]  # 最新的在前
    assert items[0].title == "外資調高目標價"
    assert items[0].url == "https://ctee.example/b"
    assert items[0].published == "2026-08-02"


async def test_tw_falls_back_to_rss_when_finmind_is_empty(monkeypatch):
    seen = _patch_client(monkeypatch, {
        "finmindtrade.com": _Resp(200, {"data": []}),
        "news.google.com": RSS_OK,
    })

    items = await news_feed.fetch_headlines("2330", "台積電", "TW")

    assert [i.source for i in items] == ["中央社"]
    assert items[0].published == "2026-08-03"
    assert any("finmindtrade" in u for u in seen)  # 主來源有先試過


async def test_source_error_falls_through_instead_of_raising(monkeypatch):
    """單一來源壞掉不能讓整檔失敗——呼叫端只想要「有沒有新聞」。"""
    _patch_client(monkeypatch, {
        "finmindtrade.com": httpx.ConnectError("down"),
        "news.google.com": RSS_OK,
    })

    items = await news_feed.fetch_headlines("2330", "台積電", "TW")

    assert len(items) == 1


async def test_all_sources_failing_returns_empty_not_exception(monkeypatch):
    _patch_client(monkeypatch, {
        "finmindtrade.com": httpx.ConnectError("down"),
        "news.google.com": httpx.ConnectError("down"),
    })

    assert await news_feed.fetch_headlines("2330", "台積電", "TW") == []


# ---- 美股：Finnhub ----

async def test_us_uses_finnhub_when_token_present(monkeypatch):
    monkeypatch.setattr(get_settings(), "finnhub_token", "test-token")
    _patch_client(monkeypatch, {"finnhub.io": _Resp(200, [
        {"headline": "Apple beats estimates", "source": "Reuters",
         "url": "https://reuters.example/a", "datetime": 1785000000},
    ])})

    items = await news_feed.fetch_headlines("AAPL", "Apple", "US")

    assert items[0].title == "Apple beats estimates"
    assert items[0].source == "Reuters"


async def test_us_without_token_skips_finnhub_and_uses_rss(monkeypatch):
    """沒設 token 不該白打 Finnhub，直接讓位給免金鑰的 RSS。"""
    monkeypatch.setattr(get_settings(), "finnhub_token", "")
    seen = _patch_client(monkeypatch, {"news.google.com": RSS_OK})

    items = await news_feed.fetch_headlines("AAPL", "Apple", "US")

    assert len(items) == 1
    assert not any("finnhub" in u for u in seen)


# ---- 共同約束 ----

async def test_item_count_is_capped(monkeypatch):
    """給 AI 的清單要有上限，否則 prompt 會被長尾新聞灌爆。"""
    rows = [{"date": f"2026-08-01 0{i%10}:00:00", "title": f"新聞{i} - 媒體",
             "link": f"https://x.example/{i}"} for i in range(40)]
    _patch_client(monkeypatch, {"finmindtrade.com": _Resp(200, {"data": rows})})

    items = await news_feed.fetch_headlines("2330", "台積電", "TW")

    assert len(items) == news_feed.MAX_ITEMS


@pytest.mark.parametrize("value,expected", [
    ("Mon, 03 Aug 2026 01:23:45 GMT", "2026-08-03"),
    ("Tue, 9 Dec 2025 10:00:00 +0800", "2025-12-09"),
    ("", ""),
    ("garbage", ""),
])
def test_rss_date_parsing(value, expected):
    assert news_feed._rss_date(value) == expected


def test_rss_parsing_unescapes_and_handles_cdata():
    xml = ("<rss><channel><item>"
           "<title><![CDATA[台積電 &amp; 聯電走揚 - 鉅亨網]]></title>"
           "<link>https://cnyes.example/z</link></item></channel></rss>")
    items = news_feed._parse_rss(xml)
    assert items[0].title == "台積電 & 聯電走揚"
    assert items[0].source == "鉅亨網"
