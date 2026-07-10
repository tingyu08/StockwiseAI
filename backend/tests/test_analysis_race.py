"""併發競態：連按「產生今日簡報」不得重複扣 AI 額度，撞 UNIQUE 不得 500。"""
import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import AiReport, DailyPrice, Stock, WatchlistItem
from app.models.analysis import AiOverview
from app.providers.ai.schemas import (
    AnalysisReport,
    BatchAnalysisResult,
    DailyBriefing,
    GlobalModule,
    LocalMarketModule,
    RiskModule,
    Scenario,
    Scenarios,
)
from app.providers.ai.base import AnalysisContext
from app.services import analysis_service


def _seed_stock(db, symbol, market="US"):
    stock = Stock(symbol=symbol, market=market, name=f"測試{symbol}", currency="USD", kind="stock")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    d = date.today() - timedelta(days=100)
    added = 0
    while added < 40:
        if d.weekday() < 5:
            db.add(DailyPrice(stock_id=stock.id, date=d, open=100, high=101, low=99, close=100, volume=1000))
            added += 1
        d += timedelta(days=1)
    db.add(WatchlistItem(stock_id=stock.id))
    db.commit()
    return stock


def _report(symbol) -> AnalysisReport:
    bull = Scenario(target_price=110, trigger_condition="t", probability=0.3)
    base = Scenario(target_price=100, trigger_condition="t", probability=0.5)
    bear = Scenario(target_price=90, trigger_condition="t", probability=0.2)
    return AnalysisReport(
        symbol=symbol, action="buy", confidence=0.8,
        target_price_low=90, target_price_high=120, stop_loss=80,
        reasoning="測試", scenarios=Scenarios(bull=bull, base=base, bear=bear),
        risks=["測試風險"],
    )


def _briefing() -> DailyBriefing:
    return DailyBriefing(
        global_market=GlobalModule(
            index_comments=["a"], key_stocks_comment="b",
            risk_sentiment="risk_neutral", one_liner="c",
        ),
        local_market=LocalMarketModule(
            support=1, resistance=2, levels_rationale="r", flow_comment="f",
            prediction="震盪整理", prediction_rationales=["x"],
        ),
        stock_notes=[],
        risks=RiskModule(events=[], black_swan_watch=[], monitor_signals=[]),
        overall_stance="neutral",
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # pytest-asyncio 每個測試開新 event loop，asyncio.Lock 綁定舊 loop 會炸；
    # 正式環境只有一個 loop，不受影響
    analysis_service._overview_locks.clear()

    async def fake_market_context(market):
        return "【市場數據】測試"

    monkeypatch.setattr("app.services.market_context.build_market_context", fake_market_context)
    yield
    # 清掉本檔種下的資料，避免污染其他測試（run_overview 會掃整個市場的自選）
    db = SessionLocal()
    try:
        for stock in db.execute(select(Stock).where(Stock.name.like("測試9%"))).scalars().all():
            for model in (AiReport, DailyPrice, WatchlistItem):
                for row in db.execute(select(model).where(model.stock_id == stock.id)).scalars():
                    db.delete(row)
            db.delete(stock)
        for row in db.execute(select(AiOverview)).scalars():
            db.delete(row)
        db.commit()
    finally:
        db.close()


async def test_concurrent_run_overview_calls_ai_once(monkeypatch):
    """兩個請求同時 run_overview：AI 只呼叫一次，只落地一份總評。"""
    db1, db2 = SessionLocal(), SessionLocal()
    try:
        _seed_stock(db1, "9001")
        calls = {"batch": 0, "overview": 0}

        async def fake_analyze_batch(db, contexts):
            calls["batch"] += 1
            await asyncio.sleep(0.02)  # 模擬 AI 延遲，讓請求有交錯機會
            return BatchAnalysisResult(reports=[_report(c.symbol) for c in contexts]), "fake"

        async def fake_generate_structured(db, prompt, output_model):
            calls["overview"] += 1
            await asyncio.sleep(0.02)
            return _briefing(), "fake"

        monkeypatch.setattr("app.providers.ai.router.analyze_batch", fake_analyze_batch)
        monkeypatch.setattr(
            "app.providers.ai.router.generate_premium_structured",
            fake_generate_structured,
        )

        r1, r2 = await asyncio.gather(
            analysis_service.run_overview(db1, "US"),
            analysis_service.run_overview(db2, "US"),
        )

        assert calls == {"batch": 1, "overview": 1}
        assert r1.id == r2.id
        count = db1.execute(select(func.count()).select_from(AiOverview)).scalar()
        assert count == 1
    finally:
        db1.close()
        db2.close()


async def test_overview_force_rebuild_bypasses_same_input_cache(monkeypatch):
    db = SessionLocal()
    try:
        _seed_stock(db, "9007")
        calls = 0

        async def fake_analyze_batch(_db, contexts):
            return BatchAnalysisResult(
                reports=[_report(context.symbol) for context in contexts]
            ), "fake"

        async def fake_generate(_db, prompt, output_model):
            nonlocal calls
            calls += 1
            return _briefing(), "fake"

        monkeypatch.setattr("app.providers.ai.router.analyze_batch", fake_analyze_batch)
        monkeypatch.setattr(
            "app.providers.ai.router.generate_premium_structured", fake_generate
        )

        await analysis_service.run_overview(db, "US")
        await analysis_service.run_overview(db, "US")
        await analysis_service.run_overview(db, "US", force=True)

        assert calls == 2
    finally:
        db.close()


async def test_run_overview_unique_conflict_returns_existing(monkeypatch):
    """commit 撞 UNIQUE(market, trade_date)（如多進程部署）：回傳既有總評而非 500。"""
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9002")
        trade_date = analysis_service._last_trade_date(db, stock)

        async def fake_analyze_batch(_db, contexts):
            return BatchAnalysisResult(reports=[_report(c.symbol) for c in contexts]), "fake"

        async def fake_generate_structured(_db, prompt, output_model):
            # 在 AI 生成期間，另一個進程搶先寫入同日總評
            other = SessionLocal()
            other.add(AiOverview(market="US", trade_date=trade_date, model="other",
                                 payload_json=_briefing().model_dump_json()))
            other.commit()
            other.close()
            return _briefing(), "fake"

        monkeypatch.setattr("app.providers.ai.router.analyze_batch", fake_analyze_batch)
        monkeypatch.setattr(
            "app.providers.ai.router.generate_premium_structured",
            fake_generate_structured,
        )

        result = await analysis_service.run_overview(db, "US")

        assert result.model == "other"  # 拿到搶先寫入的那份
        count = db.execute(select(func.count()).select_from(AiOverview)).scalar()
        assert count == 1
    finally:
        db.close()


async def test_run_batch_unique_conflict_upserts_current_input(monkeypatch):
    """批次 commit 撞 UNIQUE：保留單列並更新成目前輸入的結果。"""
    db = SessionLocal()
    try:
        s1 = _seed_stock(db, "9003")
        s2 = _seed_stock(db, "9004")
        trade_date = analysis_service._last_trade_date(db, s1)

        async def fake_analyze_batch(_db, contexts):
            # AI 呼叫期間，另一個請求搶先寫入 s1 的同日報告
            other = SessionLocal()
            other.add(AiReport(stock_id=s1.id, trade_date=trade_date, provider="test",
                               model="other", prompt_version="v1", kind="routine",
                               action="hold", confidence=0.5,
                               payload_json=_report(s1.symbol).model_dump_json()))
            other.commit()
            other.close()
            return BatchAnalysisResult(reports=[_report(c.symbol) for c in contexts]), "fake"

        monkeypatch.setattr("app.providers.ai.router.analyze_batch", fake_analyze_batch)

        result = await analysis_service.run_batch(db, [s1, s2], kind="routine")

        assert result["analyzed"] == 2
        for s in (s1, s2):
            assert analysis_service._report_exists(db, s.id, trade_date, "routine")
        # s1 沒有重複，且更新為本次輸入雜湊的結果
        rows = db.execute(
            select(AiReport).where(AiReport.stock_id == s1.id, AiReport.kind == "routine")
        ).scalars().all()
        assert len(rows) == 1 and rows[0].model == "fake" and rows[0].input_hash
    finally:
        db.close()


async def test_trade_batch_uses_premium_router(monkeypatch):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9005")

        async def fail_routine(*args, **kwargs):
            raise AssertionError("trade batch must not use the routine chain first")

        async def fake_trade(_db, contexts):
            return BatchAnalysisResult(
                reports=[_report(context.symbol) for context in contexts]
            ), "gemini-3.5-flash"

        monkeypatch.setattr("app.providers.ai.router.analyze_batch", fail_routine)
        monkeypatch.setattr(
            "app.providers.ai.router.analyze_trading_batch", fake_trade
        )

        result = await analysis_service.run_batch(db, [stock], kind="trade")

        assert result["model"] == "gemini-3.5-flash"
        report = analysis_service.latest_report(db, stock, kinds=("trade",))
        assert report is not None and report.kind == "trade"
    finally:
        db.close()


async def test_run_batch_rebuilds_when_input_context_changes(monkeypatch):
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9006")
        state = {"news": "第一版", "calls": 0}

        async def fake_context(_db, current_stock):
            return AnalysisContext(
                symbol=current_stock.symbol,
                market=current_stock.market,
                price_summary="價格固定",
                news_summary=state["news"],
            )

        async def fake_analyze(_db, contexts):
            state["calls"] += 1
            return BatchAnalysisResult(
                reports=[_report(context.symbol) for context in contexts]
            ), "fake"

        monkeypatch.setattr(analysis_service, "build_context", fake_context)
        monkeypatch.setattr("app.providers.ai.router.analyze_batch", fake_analyze)

        await analysis_service.run_batch(db, [stock], kind="routine")
        await analysis_service.run_batch(db, [stock], kind="routine")
        state["news"] = "第二版"
        await analysis_service.run_batch(db, [stock], kind="routine")

        assert state["calls"] == 2
        rows = db.execute(
            select(AiReport).where(
                AiReport.stock_id == stock.id, AiReport.kind == "routine"
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].input_hash
    finally:
        db.close()


async def test_run_deep_unique_conflict_returns_existing(monkeypatch):
    """深度分析 commit 撞 UNIQUE：回傳既有報告而非 500。"""
    db = SessionLocal()
    try:
        stock = _seed_stock(db, "9005")
        trade_date = analysis_service._last_trade_date(db, stock)

        async def fake_analyze_deep(_db, context):
            other = SessionLocal()
            other.add(AiReport(stock_id=stock.id, trade_date=trade_date, provider="test",
                               model="other", prompt_version="v1", kind="deep",
                               action="hold", confidence=0.5,
                               payload_json=_report(stock.symbol).model_dump_json()))
            other.commit()
            other.close()
            return _report(stock.symbol), "fake"

        monkeypatch.setattr("app.providers.ai.router.analyze_deep", fake_analyze_deep)

        result = await analysis_service.run_deep(db, stock)

        assert result.model == "other"
        rows = db.execute(
            select(AiReport).where(AiReport.stock_id == stock.id, AiReport.kind == "deep")
        ).scalars().all()
        assert len(rows) == 1
    finally:
        db.close()
