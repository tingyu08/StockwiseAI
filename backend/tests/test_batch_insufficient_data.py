"""一檔資料不足不得炸掉整批分析。

正式環境實際發生過：自選清單裡一檔新上市的主動式 ETF（00407A，日線不足
30 筆）讓 overview-tw 每天 06:55 固定失敗，「AI 每日投資簡報」連續數日
停在同一天。build_context 對資料不足丟 NotFoundError，而 run_batch 沒有
攔它——一檔就讓整份簡報產不出來。

簡報取的是「整份自選清單」（不像例行批次只取 AI 託管），更容易混進這種
還沒累積夠歷史的標的，所以這條路徑特別脆弱。
"""
from datetime import date, timedelta

import pytest

from app.core.db import SessionLocal
from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Stock, WatchlistItem
from app.services import analysis_service


def _seed(db, symbol, *, days: int, ai_managed=False, market="TW"):
    """建立一檔股票並塞 days 筆日線（days<30 即為『資料不足』）。"""
    stock = Stock(symbol=symbol, market=market, name=f"不足{symbol}",
                  currency="TWD", kind="stock")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    start = date(2026, 1, 5)
    for i in range(days):
        d = start + timedelta(days=i)
        db.add(DailyPrice(stock_id=stock.id, date=d, open=100.0, high=101.0,
                          low=99.0, close=100.0 + i * 0.1, volume=1000))
    db.add(WatchlistItem(stock_id=stock.id, ai_managed=ai_managed))
    db.commit()
    return stock


_PAYLOAD = (
    '{"action":"hold","confidence":0.5,"target_price_low":90,'
    '"target_price_high":110,"stop_loss":85,"reasoning":"測試用"}'
)


@pytest.fixture
def _stub_ai(monkeypatch):
    """讓批次分析不真的呼叫 AI，也不打 FinMind（測試 token 是假的）。"""
    class _Report:
        def __init__(self, symbol):
            self.symbol = symbol
            self.action = "hold"
            self.confidence = 0.5

        def model_dump_json(self):
            # 必須是完整 payload：_run_overview 會讀 confidence／目標價／停損，
            # 缺欄位會以 KeyError 炸掉，那是測試的問題不是程式的問題
            return _PAYLOAD

    class _Result:
        def __init__(self, contexts):
            self.reports = [_Report(c.symbol) for c in contexts]

    async def fake_batch(db, contexts):
        return _Result(contexts), "stub-model"

    async def no_tw_facts(symbol, is_etf=False):
        return "", ""

    monkeypatch.setattr(analysis_service.ai_router, "analyze_batch", fake_batch)
    monkeypatch.setattr(analysis_service.ai_router, "analyze_trading_batch", fake_batch)
    monkeypatch.setattr(
        "app.services.tw_market_facts.build_tw_facts", no_tw_facts
    )


async def test_one_thin_history_stock_does_not_kill_the_batch(client, _stub_ai):
    db = SessionLocal()
    try:
        thin = _seed(db, "THIN01", days=5)      # 新上市，日線不足
        ok1 = _seed(db, "FULL01", days=60)
        ok2 = _seed(db, "FULL02", days=60)

        result = await analysis_service.run_batch(db, [thin, ok1, ok2])

        assert result["insufficient"] == ["THIN01"]
        assert result["analyzed"] == 2          # 另外兩檔照常完成
    finally:
        db.close()


async def test_batch_of_only_thin_stocks_returns_instead_of_raising(client, _stub_ai):
    """全部都資料不足時要正常回傳，不可讓呼叫端（簡報）整個失敗。"""
    db = SessionLocal()
    try:
        thin = _seed(db, "THIN02", days=3)

        result = await analysis_service.run_batch(db, [thin])

        assert result["analyzed"] == 0
        assert result["insufficient"] == ["THIN02"]
    finally:
        db.close()


async def test_overview_survives_a_thin_history_stock(client, _stub_ai, monkeypatch):
    """契約鎖在真正出事的路徑：整份自選清單裡有一檔資料不足時，
    每日簡報仍須產得出來。"""
    async def fake_market_context(market):
        return "【市場環境】測試用"

    monkeypatch.setattr(
        "app.services.market_context.build_market_context", fake_market_context
    )

    captured = {}

    async def fake_premium(db, prompt, schema):
        captured["prompt"] = prompt

        class _B:
            def model_dump_json(self):
                return '{"overall_stance":"neutral"}'

        return _B(), "stub-premium"

    monkeypatch.setattr(
        "app.providers.ai.router.generate_premium_structured", fake_premium
    )

    db = SessionLocal()
    try:
        _seed(db, "THIN03", days=4)
        _seed(db, "FULL03", days=60)

        overview = await analysis_service.run_overview(db, "TW")

        assert overview is not None
        # 資料不足的那檔仍會出現在簡報輸入裡，只是標明沒有 AI 報告——
        # 悄悄從清單消失會讓使用者以為那檔沒被追蹤
        assert "THIN03" in captured["prompt"]
        assert "尚無 AI 報告" in captured["prompt"]
    finally:
        db.close()


async def test_single_stock_request_still_reports_the_real_reason(client):
    """單檔請求被跳過時必須說出真正原因，不能含糊成「請稍後再試」。"""
    db = SessionLocal()
    try:
        _seed(db, "THIN04", days=2)
    finally:
        db.close()

    res = client.post("/api/v1/stocks/THIN04/analysis:routine?market=TW")

    assert res.status_code == 404
    assert "價格資料不足" in res.json()["error"]


def test_build_context_still_guards_thin_history(client):
    """底層守門不可被移除——它才是「不拿殘缺資料餵 AI」的保證。"""
    import asyncio

    db = SessionLocal()
    try:
        thin = _seed(db, "THIN05", days=6)
        with pytest.raises(NotFoundError) as exc:
            asyncio.run(analysis_service.build_context(db, thin))
        assert "價格資料不足" in exc.value.message
    finally:
        db.close()
