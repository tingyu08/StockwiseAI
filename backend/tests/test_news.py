"""新聞面研究模組：快取、管線注入、API。新聞來源與 AI 一律 mock。"""
import json

from datetime import date, timedelta

from app.core.db import SessionLocal
from app.models import AiReport, DailyPrice, Indicator, Stock
from app.services import news_service


def _patch_news(monkeypatch, tone="偏多", items=None):
    """攔截「抓新聞」與「AI 摘要」兩段，回傳被查詢過的代號清單。"""
    from app.providers.ai.schemas import NewsBrief, NewsHighlight
    from app.providers.news_feed import NewsItem

    calls = []
    feed = items if items is not None else [
        NewsItem(published="2026-08-01", title="測試事件",
                 source="測試媒體", url="https://example.com/a")
    ]

    async def fake_fetch(symbol, name, market):
        calls.append(symbol)
        return list(feed)

    async def fake_generate(db, prompt, output_model):
        return NewsBrief(
            tone=tone, tone_reason="測試理由",
            highlights=[NewsHighlight(date="08/01", summary="測試事件",
                                      source="測試媒體",
                                      url="https://example.com/a")],
        ), "gemini-3.5-flash-lite"

    monkeypatch.setattr("app.providers.news_feed.fetch_headlines", fake_fetch)
    monkeypatch.setattr(
        "app.providers.ai.router.generate_structured", fake_generate
    )
    return calls


def test_prompt_forces_the_model_to_reuse_our_urls():
    """出處必須沿用我們給的網址——AI 不再自己上網，也就不該自己生出處。"""
    from app.providers.news_feed import NewsItem

    stock = Stock(symbol="2330", market="TW", name="台積電",
                  currency="TWD", kind="stock")
    items = [NewsItem(published="2026-08-01", title="法說會優於預期",
                      source="經濟日報", url="https://example.com/a")]
    prompt = news_service._summary_prompt(stock, items)

    assert "https://example.com/a" in prompt
    assert "原封不動沿用" in prompt
    assert "不要加入清單以外的資訊" in prompt


def _seed_stock(db, symbol, market="TW", with_prices=False):
    stock = Stock(symbol=symbol, market=market, name=f"新聞{symbol}", currency="TWD", kind="stock")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    if with_prices:
        for i in range(40):
            d = date.today() - timedelta(days=40 - i)
            db.add(DailyPrice(stock_id=stock.id, date=d,
                              open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1000))
        db.add(Indicator(stock_id=stock.id, date=date.today() - timedelta(days=1), ma5=100, ma20=95))
        db.commit()
    return stock


def _seed_news(db, stock, days_ago=0, summary="測試新聞摘要"):
    row = AiReport(
        stock_id=stock.id,
        trade_date=date.today() - timedelta(days=days_ago),
        provider="gemini",
        model="gemini-3.5-flash-lite",
        prompt_version="news-v1",
        kind="news",
        payload_json=json.dumps({"summary": summary}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return row


async def test_news_research_daily_cache(monkeypatch):
    """當日已有 news 報告 → 不再呼叫 Antigravity。"""
    calls = _patch_news(monkeypatch)
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "7101")
        r1 = await news_service.run_news_research(db, stock)
        r2 = await news_service.run_news_research(db, stock)
        assert r1.id == r2.id  # 第二次吃快取
        assert calls == ["7101"]
        assert "偏多" in json.loads(r1.payload_json)["summary"]
    finally:
        db.close()


def test_latest_news_summary_freshness():
    """保鮮期內注入、過期回空字串。"""
    db = SessionLocal()
    try:
        fresh = _seed_stock(db, "7102")
        _seed_news(db, fresh, days_ago=2, summary="近況良好")
        assert "近況良好" in news_service.latest_news_summary(db, fresh)

        stale = _seed_stock(db, "7103")
        _seed_news(db, stale, days_ago=news_service.FRESH_DAYS + 1)
        assert news_service.latest_news_summary(db, stale) == ""
    finally:
        db.close()


async def test_build_context_injects_news():
    """分析管線輸入要帶到 news_summary。"""
    from app.services.analysis_service import build_context

    db = SessionLocal()
    try:
        stock = _seed_stock(db, "7104", market="US", with_prices=True)
        _seed_news(db, stock, days_ago=1, summary="財報優於預期")
        ctx = await build_context(db, stock)
        assert "財報優於預期" in ctx.news_summary
    finally:
        db.close()


def test_news_api_get_and_404(client):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "7105")
        _seed_news(db, stock, summary="API 測試摘要")
        _seed_stock(db, "7106")
    finally:
        db.close()

    res = client.get("/api/v1/stocks/7105/news?market=TW")
    assert res.status_code == 200
    assert res.json()["data"]["summary"] == "API 測試摘要"

    res = client.get("/api/v1/stocks/7106/news?market=TW")
    assert res.status_code == 404


def test_news_api_run_triggers_research(client, monkeypatch):
    _patch_news(monkeypatch)
    db = SessionLocal()
    try:
        _seed_stock(db, "7107")
    finally:
        db.close()

    res = client.post("/api/v1/stocks/7107/news:run?market=TW")
    assert res.status_code == 200
    assert res.json()["data"]["started"] is True
    assert isinstance(res.json()["data"]["run_id"], int)
    run_id = res.json()["data"]["run_id"]
    db = SessionLocal()
    try:
        from app.models import JobRun

        run = db.get(JobRun, run_id)
        assert run.job_type == "news"
        assert json.loads(run.payload_json) == {"market": "TW", "symbol": "7107"}
    finally:
        db.delete(run)
        db.commit()
        db.close()


async def test_news_force_refresh_updates_same_daily_row(monkeypatch):
    """force 重跑要覆寫同一天那一列，而不是新增一列（DB 有唯一約束）。"""
    _patch_news(monkeypatch, tone="偏多")
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "7111")
        first = await news_service.run_news_research(db, stock)
        cached = await news_service.run_news_research(db, stock)

        _patch_news(monkeypatch, tone="偏空")  # 第二版：基調不同
        refreshed = await news_service.run_news_research(db, stock, force=True)

        assert first.id == cached.id == refreshed.id
        assert "偏空" in json.loads(refreshed.payload_json)["summary"]
    finally:
        db.close()


async def test_news_job_stops_on_quota(monkeypatch):
    """額度盡 → 提前收工，不炸整個 job。"""
    from app.core.exceptions import QuotaExceededError
    from app.models import WatchlistItem
    from app.scheduler.jobs import news_research_daily

    # 交易日閘門與本測試無關；不擋掉的話週末/假日會提前 return
    # {"skipped": ...}，測試每逢週末必失敗（CI 也是）
    monkeypatch.setattr("app.scheduler.jobs._non_trading_gate", lambda market: None)

    calls = []

    async def fake_research(db, stock, force=False):
        calls.append(stock.symbol)
        if len(calls) >= 2:
            raise QuotaExceededError("今日免費額度已用盡", scope="rpd")
        return None

    monkeypatch.setattr("app.services.news_service.run_news_research", fake_research)
    db = SessionLocal()
    try:
        for sym in ("7108", "7109", "7110"):
            stock = _seed_stock(db, sym)
            db.add(WatchlistItem(stock_id=stock.id, ai_managed=True))
        db.commit()
    finally:
        db.close()

    result = await news_research_daily("TW")
    assert result["researched"] == 1
    assert len(calls) == 2  # 第二檔遇到額度盡即 break，第三檔不再呼叫
    assert result["failed"] == []


# ---- 沒有新聞時的行為 ----

async def test_no_headlines_records_honestly_without_calling_ai(monkeypatch):
    """來源都查無資料時要如實記錄，不可為了產出而叫 AI 生一段話。"""
    ai_calls = []

    async def no_news(symbol, name, market):
        return []

    async def fake_generate(db, prompt, output_model):
        ai_calls.append(prompt)
        raise AssertionError("沒有新聞就不該呼叫 AI")

    monkeypatch.setattr("app.providers.news_feed.fetch_headlines", no_news)
    monkeypatch.setattr("app.providers.ai.router.generate_structured", fake_generate)

    db = SessionLocal()
    try:
        stock = _seed_stock(db, "7120")
        row = await news_service.run_news_research(db, stock)

        assert json.loads(row.payload_json)["summary"] == "近 7 天無重大新聞"
        assert row.model == "none"
        assert ai_calls == []
    finally:
        db.close()


def test_render_keeps_the_plain_text_contract():
    """下游（分析管線注入、前端顯示）吃的是純文字，格式不能變。"""
    from app.providers.ai.schemas import NewsBrief, NewsHighlight

    brief = NewsBrief(
        tone="偏空", tone_reason="訂單能見度下修",
        highlights=[
            NewsHighlight(date="08/01", summary="法說會下修財測",
                          source="經濟日報", url="https://money.example/a"),
        ],
    )
    text = news_service._render(brief)

    assert text.splitlines()[0] == "偏空——訂單能見度下修"
    assert "08/01 法說會下修財測（經濟日報｜https://money.example/a）" in text
