"""新聞研究排程的額度處理：分鐘級限流不得被當成「今日額度用盡」。

正式環境每天早上都會發生：Antigravity 單檔約 34K tokens、TPM 上限 100K，
agent 任務跑得快時三檔擠進同一個滾動分鐘就被本地 rate limiter 擋下。
舊版把所有 QuotaExceededError 都當成今日用盡而 break，清單順序固定，
等於後段的股票永遠拿不到新聞研究（RPD 100 其實只用掉個位數）。
"""
import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.exceptions import QuotaExceededError
from app.models import Stock, WatchlistItem
from app.scheduler import jobs


@pytest.fixture(autouse=True)
def _trading_day_and_no_waiting(monkeypatch):
    monkeypatch.setattr("app.scheduler.jobs.is_trading_day", lambda m, d: True)
    monkeypatch.setattr("app.scheduler.jobs.NEWS_QUOTA_WAIT_SEC", 0)


@pytest.fixture(autouse=True)
def _only_this_test_is_ai_managed():
    """測試共用同一個 test.db，其他測試留下的 ai_managed 股票會混進本檔的
    託管清單，讓「跑了幾檔」的斷言失去意義。先停用、跑完再還原。"""
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
        # 先全部關掉再開回原本那批：只還原是不夠的，本檔自己種的託管股票
        # 會留在共用 DB 裡，污染後面檔案的託管清單
        for item in db.execute(select(WatchlistItem)).scalars():
            item.ai_managed = item.id in ids
        db.commit()
    finally:
        db.close()


def _seed_managed(db, symbols, market="TW"):
    for symbol in symbols:
        stock = Stock(
            symbol=symbol, market=market, name=f"額度{symbol}",
            currency="TWD", kind="stock",
        )
        db.add(stock)
        db.commit()
        db.refresh(stock)
        db.add(WatchlistItem(stock_id=stock.id, ai_managed=True))
    db.commit()


def _patch_research(monkeypatch, behaviour):
    """behaviour: symbol -> 例外或 None（成功）。可用 list 表示逐次不同結果。"""
    calls = []

    async def fake(db, stock, force=False):
        calls.append(stock.symbol)
        outcome = behaviour.get(stock.symbol)
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if outcome else None
        if outcome is not None:
            raise outcome
        return None

    monkeypatch.setattr("app.services.news_service.run_news_research", fake)
    return calls


def test_quota_error_scopes_are_classified():
    assert QuotaExceededError("x", scope="tpm").retryable is True
    assert QuotaExceededError("x", scope="rpm").retryable is True
    assert QuotaExceededError("x", scope="rpd").retryable is False
    assert QuotaExceededError("x").scope == "rpd"  # 預設保守：當成今日用盡


async def test_tpm_limit_retries_and_does_not_abandon_remaining_stocks(
    client, monkeypatch
):
    """TPM 是分鐘級視窗——重試後應完成該檔，且後面的股票照跑。"""
    db = SessionLocal()
    try:
        _seed_managed(db, ["QSTPM1", "QSTPM2", "QSTPM3"])
        calls = _patch_research(monkeypatch, {
            "QSTPM2": [QuotaExceededError("TPM 額度已用盡", scope="tpm")],
        })

        result = await jobs.news_research_daily("TW")

        assert result["researched"] == 3          # 三檔全數完成
        assert result["skipped"] == []
        assert result["quota_stopped"] is None
        assert calls.count("QSTPM2") == 2           # 撞到後重試了一次
        assert "QSTPM3" in calls                    # 關鍵：後面的沒被放棄
    finally:
        db.close()


async def test_daily_quota_exhaustion_stops_early_and_records_what_was_skipped(
    client, monkeypatch
):
    """RPD 才是真的今日沒了：提前收工，但要留下沒跑到哪些檔的紀錄。"""
    db = SessionLocal()
    try:
        _seed_managed(db, ["QSRPD1", "QSRPD2", "QSRPD3"])
        _patch_research(monkeypatch, {
            "QSRPD2": QuotaExceededError("今日免費額度已用盡", scope="rpd"),
            "QSRPD3": QuotaExceededError("不該被呼叫", scope="rpd"),
        })

        result = await jobs.news_research_daily("TW")

        assert result["researched"] == 1
        assert result["quota_stopped"] == "今日免費額度已用盡"
        assert set(result["skipped"]) == {"QSRPD2", "QSRPD3"}
    finally:
        db.close()


async def test_upstream_429_stops_early(client, monkeypatch):
    """Google 端 429 無從分辨是哪種限流，保守收工而非狂打已限流的 API。"""
    db = SessionLocal()
    try:
        _seed_managed(db, ["QS4291", "QS4292"])
        calls = _patch_research(monkeypatch, {
            "QS4291": QuotaExceededError("Antigravity 被 Google 端限流（429）",
                                       scope="upstream"),
        })

        result = await jobs.news_research_daily("TW")

        assert result["researched"] == 0
        assert calls == ["QS4291"]  # 沒有重試、也沒往下打
        assert set(result["skipped"]) == {"QS4291", "QS4292"}
    finally:
        db.close()


async def test_persistent_minute_limit_skips_one_stock_but_continues(client, monkeypatch):
    """重試用盡只放棄該檔，不是放棄整批。"""
    db = SessionLocal()
    try:
        _seed_managed(db, ["QSMIN1", "QSMIN2"])
        tpm = QuotaExceededError("TPM 額度已用盡", scope="tpm")
        calls = _patch_research(monkeypatch, {"QSMIN1": [tpm, tpm, tpm]})

        result = await jobs.news_research_daily("TW")

        assert result["skipped"] == ["QSMIN1"]
        assert result["researched"] == 1
        assert calls.count("QSMIN1") == jobs.NEWS_QUOTA_RETRIES
        assert "QSMIN2" in calls
    finally:
        db.close()


async def test_misconfigured_quota_is_not_disguised_as_exhaustion(client, monkeypatch):
    """quotas.yaml 漏設模型是設定錯誤，必須讓工作失敗而非每天偽裝成額度用盡。"""
    db = SessionLocal()
    try:
        _seed_managed(db, ["QSCFG1"])
        _patch_research(monkeypatch, {
            "QSCFG1": QuotaExceededError("未設定 X 的額度", scope="config"),
        })

        with pytest.raises(QuotaExceededError):
            await jobs.news_research_daily("TW")
    finally:
        db.close()
