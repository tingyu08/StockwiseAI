"""interaction 連續讀不到時要熔斷，不能逐檔重建到天亮。

正式環境 08/03：Antigravity 的 v1beta/interactions 整批回 403
permission_denied（同一把 API key 的 models/*:generateContent 全部正常），
而每檔失敗會先重建一次新任務，於是變成

    12 檔 × 45 秒 ≈ 9 分鐘、燒掉 24 次 RPD，且一檔都沒救回來

零星一檔壞掉時重建仍有價值（那是常態），所以熔斷門檻設 2 而非 1。
"""
import pytest

from app.core.db import SessionLocal
from app.core.exceptions import QuotaExceededError, UpstreamError
from app.models import Stock, WatchlistItem
from app.providers.ai.antigravity import InteractionUnreadableError
from app.scheduler import jobs
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _trading_day_and_no_waiting(monkeypatch):
    monkeypatch.setattr("app.scheduler.jobs.is_trading_day", lambda m, d: True)
    monkeypatch.setattr("app.scheduler.jobs.NEWS_QUOTA_WAIT_SEC", 0)


@pytest.fixture(autouse=True)
def _only_this_test_is_ai_managed():
    db = SessionLocal()
    try:
        previously = db.execute(
            select(WatchlistItem).where(WatchlistItem.ai_managed.is_(True))
        ).scalars().all()
        ids = [item.id for item in previously]
        for item in previously:
            item.ai_managed = False
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        for item in db.execute(select(WatchlistItem)).scalars():
            item.ai_managed = item.id in ids
        db.commit()
    finally:
        db.close()


def _seed(db, symbols):
    for symbol in symbols:
        stock = Stock(symbol=symbol, market="TW", name=f"熔斷{symbol}",
                      currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        db.add(WatchlistItem(stock_id=stock.id, ai_managed=True))
    db.commit()


def _patch(monkeypatch, behaviour):
    calls = []

    async def fake(db, stock, force=False):
        calls.append(stock.symbol)
        outcome = behaviour.get(stock.symbol)
        if isinstance(outcome, Exception):
            raise outcome
        return None

    monkeypatch.setattr("app.services.news_service.run_news_research", fake)
    return calls


UNREADABLE = InteractionUnreadableError("讀不到（403，等待 20s）")


async def test_consecutive_unreadable_stops_the_whole_run(client, monkeypatch):
    db = SessionLocal()
    try:
        _seed(db, ["BRK01", "BRK02", "BRK03", "BRK04", "BRK05"])
        calls = _patch(monkeypatch, {s: UNREADABLE for s in
                                    ["BRK01", "BRK02", "BRK03", "BRK04", "BRK05"]})

        result = await jobs.news_research_daily("TW")

        # 熔斷在第 2 檔，後面 3 檔完全沒有被嘗試——省下時間與額度
        assert calls == ["BRK01", "BRK02"]
        assert result["failed"] == ["BRK01", "BRK02"]
        assert set(result["skipped"]) == {"BRK03", "BRK04", "BRK05"}
        assert "上游異常" in result["upstream_stopped"]
    finally:
        db.close()


async def test_isolated_unreadable_does_not_stop_the_run(client, monkeypatch):
    """零星一檔讀不到是常態，不可因此放棄其餘標的。"""
    db = SessionLocal()
    try:
        _seed(db, ["ISO01", "ISO02", "ISO03"])
        calls = _patch(monkeypatch, {"ISO02": UNREADABLE})

        result = await jobs.news_research_daily("TW")

        assert calls == ["ISO01", "ISO02", "ISO03"]
        assert result["researched"] == 2
        assert result["failed"] == ["ISO02"]
        assert result["upstream_stopped"] is None
    finally:
        db.close()


async def test_success_resets_the_streak(client, monkeypatch):
    """失敗-成功-失敗不是「連續」，不該熔斷。"""
    db = SessionLocal()
    try:
        _seed(db, ["RST01", "RST02", "RST03", "RST04"])
        calls = _patch(monkeypatch, {"RST01": UNREADABLE, "RST03": UNREADABLE})

        result = await jobs.news_research_daily("TW")

        assert calls == ["RST01", "RST02", "RST03", "RST04"]
        assert result["upstream_stopped"] is None
        assert result["researched"] == 2
    finally:
        db.close()


async def test_other_upstream_errors_do_not_trip_the_breaker(client, monkeypatch):
    """熔斷只針對「讀不到」。逾時等其他錯誤沿用既有的逐檔容錯。"""
    db = SessionLocal()
    try:
        _seed(db, ["OTH01", "OTH02", "OTH03"])
        calls = _patch(monkeypatch, {
            "OTH01": UpstreamError("任務逾時（>480s）"),
            "OTH02": UpstreamError("回傳空白結果"),
        })

        result = await jobs.news_research_daily("TW")

        assert calls == ["OTH01", "OTH02", "OTH03"]
        assert result["upstream_stopped"] is None
    finally:
        db.close()


async def test_quota_stop_still_works_alongside_the_breaker(client, monkeypatch):
    """既有的額度收工行為不得被破壞。"""
    db = SessionLocal()
    try:
        _seed(db, ["QTA01", "QTA02"])
        _patch(monkeypatch, {
            "QTA01": QuotaExceededError("今日免費額度已用盡", scope="rpd"),
        })

        result = await jobs.news_research_daily("TW")

        assert result["quota_stopped"] == "今日免費額度已用盡"
        assert result["upstream_stopped"] is None
        assert set(result["skipped"]) == {"QTA01", "QTA02"}
    finally:
        db.close()
