"""新聞面研究模組：快取、管線注入、API。Antigravity 呼叫一律 mock。"""
import json
from datetime import date, timedelta

from app.core.db import SessionLocal
from app.models import AiReport, DailyPrice, Indicator, Stock
from app.services import news_service


def test_news_prompt_requires_traceable_source_urls():
    from app.providers.ai.antigravity import NEWS_PROMPT_TEMPLATE

    assert "URL" in NEWS_PROMPT_TEMPLATE


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
        provider="antigravity",
        model="antigravity-preview-05-2026",
        prompt_version="news-v1",
        kind="news",
        payload_json=json.dumps({"summary": summary}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return row


async def test_news_research_daily_cache(monkeypatch):
    """當日已有 news 報告 → 不再呼叫 Antigravity。"""
    calls = []

    async def fake_research(self, symbol, name, market):
        calls.append(symbol)
        return "一句話總結：偏多。\n07/08 測試事件（測試媒體）"

    monkeypatch.setattr(
        "app.providers.ai.antigravity.AntigravityProvider.research_news", fake_research
    )
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
    async def fake_research(self, symbol, name, market):
        return "手動觸發研究結果"

    monkeypatch.setattr(
        "app.providers.ai.antigravity.AntigravityProvider.research_news", fake_research
    )
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
    responses = iter(["第一版新聞", "第二版新聞"])

    async def fake_research(self, symbol, name, market):
        return next(responses)

    monkeypatch.setattr(
        "app.providers.ai.antigravity.AntigravityProvider.research_news", fake_research
    )
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "7111")
        first = await news_service.run_news_research(db, stock)
        cached = await news_service.run_news_research(db, stock)
        refreshed = await news_service.run_news_research(db, stock, force=True)

        assert first.id == cached.id == refreshed.id
        assert json.loads(refreshed.payload_json)["summary"] == "第二版新聞"
    finally:
        db.close()


def test_extract_output_text_from_steps():
    """實測 background interaction 無頂層 output_text，從 steps 取 model_output。"""
    from app.providers.ai.antigravity import _extract_output_text

    interaction = {
        "status": "completed",
        "steps": [
            {"type": "thought", "summary": [{"text": "thinking...", "type": "text"}]},
            {"type": "google_search_call", "id": "x", "arguments": {}},
            {"type": "model_output", "content": [{"text": "新聞摘要本文", "type": "text"}]},
        ],
    }
    assert _extract_output_text(interaction) == "新聞摘要本文"
    assert _extract_output_text({"output_text": "頂層優先"}) == "頂層優先"
    assert _extract_output_text({"steps": []}) == ""


async def test_news_job_stops_on_quota(monkeypatch):
    """額度盡 → 提前收工，不炸整個 job。"""
    from app.core.exceptions import QuotaExceededError
    from app.models import WatchlistItem
    from app.scheduler.jobs import news_research_daily

    calls = []

    async def fake_research(self, symbol, name, market):
        calls.append(symbol)
        if len(calls) >= 2:
            raise QuotaExceededError("額度盡")
        return "ok"

    monkeypatch.setattr(
        "app.providers.ai.antigravity.AntigravityProvider.research_news", fake_research
    )
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
