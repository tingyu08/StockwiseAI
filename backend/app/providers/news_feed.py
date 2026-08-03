"""個股新聞來源：拿回真實的標題與網址，交給 AI 摘要。

為什麼不讓 AI 自己上網：Gemini 的 Google 搜尋接地在本專案的方案下一律回
429，而 Antigravity agent（內建搜尋）建得起任務卻讀不到結果（403）。兩者
都是 Google 端的資格問題，改程式救不了。實測同一把金鑰的純文字生成與
url_context 皆正常——所以把「找新聞」與「讀新聞」拆開：

    這個模組負責「找」（用我們已經有的資料源），AI 只負責「讀與摘要」。

好處是新聞來源自己可控、可驗證，AI 也不可能虛構出處——網址是我們給的。

來源（皆為專案既有的憑證，不需新申請）：
  台股 → FinMind TaiwanStockNews
  美股 → Finnhub company-news
  兩者失敗時 → Google News RSS（免金鑰）
"""
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

LOOKBACK_DAYS = 7
MAX_ITEMS = 12  # 給 AI 的上限：夠判斷基調，又不會把 prompt 撐爆
TIMEOUT_SEC = 20


@dataclass(frozen=True)
class NewsItem:
    published: str  # YYYY-MM-DD
    title: str
    source: str
    url: str


async def fetch_headlines(symbol: str, name: str, market: str) -> list[NewsItem]:
    """取近 LOOKBACK_DAYS 天的新聞標題。取不到回空清單（呼叫端據此記錄無新聞）。"""
    primary = _fetch_tw if market == "TW" else _fetch_us
    for source in (primary, _fetch_google_news):
        try:
            items = await source(symbol, name, market)
        except Exception:
            logger.warning("新聞來源 %s 取得失敗（%s）", source.__name__, symbol,
                           exc_info=True)
            continue
        if items:
            return items[:MAX_ITEMS]
    return []


def _since() -> date:
    return date.today() - timedelta(days=LOOKBACK_DAYS)


async def _fetch_tw(symbol: str, name: str, market: str) -> list[NewsItem]:
    token = get_settings().finmind_token
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        res = await client.get(FINMIND_URL, params={
            "dataset": "TaiwanStockNews", "data_id": symbol,
            "start_date": _since().isoformat(), "token": token,
        })
    res.raise_for_status()
    rows = res.json().get("data") or []
    items = []
    for row in reversed(rows):  # FinMind 由舊到新，最新的優先給 AI
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        # FinMind 的 title 常帶「 - 媒體名」後綴，拆出來當來源
        headline, _, source = title.rpartition(" - ")
        items.append(NewsItem(
            published=str(row.get("date") or "")[:10],
            title=(headline or title).strip(),
            source=(source or "FinMind").strip(),
            url=str(row.get("link") or "").strip(),
        ))
    return items


async def _fetch_us(symbol: str, name: str, market: str) -> list[NewsItem]:
    token = get_settings().finnhub_token.strip()
    if not token:
        return []  # 未設定就直接讓位給 RSS 備援
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        res = await client.get(FINNHUB_NEWS_URL, params={
            "symbol": symbol, "from": _since().isoformat(),
            "to": date.today().isoformat(), "token": token,
        })
    res.raise_for_status()
    rows = res.json() or []
    items = []
    for row in rows:
        headline = str(row.get("headline") or "").strip()
        if not headline:
            continue
        stamp = row.get("datetime")
        items.append(NewsItem(
            published=date.fromtimestamp(stamp).isoformat() if stamp else "",
            title=headline,
            source=str(row.get("source") or "Finnhub").strip(),
            url=str(row.get("url") or "").strip(),
        ))
    return items


async def _fetch_google_news(symbol: str, name: str, market: str) -> list[NewsItem]:
    """免金鑰備援。RSS 不是搜尋 API，不受接地額度影響。"""
    query = f"{name} 股價" if market == "TW" else f"{name} {symbol} stock"
    params = ({"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
              if market == "TW" else
              {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC, follow_redirects=True) as client:
        res = await client.get(GOOGLE_NEWS_RSS, params=params,
                               headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    return _parse_rss(res.text)


_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    if not match:
        return ""
    value = match.group(1).strip()
    if value.startswith("<![CDATA["):
        value = value[9:-3]
    return unescape(value).strip()


def _parse_rss(xml: str) -> list[NewsItem]:
    items = []
    for block in _ITEM_RE.findall(xml):
        title = _tag(block, "title")
        if not title:
            continue
        # Google News 的標題格式是「標題 - 媒體」
        headline, _, source = title.rpartition(" - ")
        items.append(NewsItem(
            published=_rss_date(_tag(block, "pubDate")),
            title=(headline or title).strip(),
            source=(source or _tag(block, "source") or "Google News").strip(),
            url=_tag(block, "link"),
        ))
    return items


_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _rss_date(value: str) -> str:
    """RFC 822（如 'Mon, 03 Aug 2026 01:23:45 GMT'）→ YYYY-MM-DD。"""
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", value)
    if not match:
        return ""
    day, mon, year = match.groups()
    month = _MONTHS.get(mon)
    return f"{year}-{month:02d}-{int(day):02d}" if month else ""
