from app.services import analysis_service
from app.scheduler.jobs import JOBS


def test_overview_jobs_are_available_to_external_scheduler():
    assert "overview-tw" in JOBS
    assert "overview-us" in JOBS


async def test_scheduled_overview_uses_the_requested_market_and_closes_session(monkeypatch):
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    db = FakeSession()
    seen = {}

    async def run_overview(session, market):
        seen.update(session=session, market=market)
        return {"market": market}

    monkeypatch.setattr("app.scheduler.jobs.SessionLocal", lambda: db)
    monkeypatch.setattr(analysis_service, "run_overview", run_overview)
    monkeypatch.setattr(analysis_service, "overview_dto", lambda overview: overview)

    result = await JOBS["overview-tw"]()

    assert result == {"market": "TW"}
    assert seen == {"session": db, "market": "TW"}
    assert db.closed is True


def _scheduled_minutes(scheduler) -> dict[str, int]:
    """{job 名稱: 當日第幾分鐘}——僅取 hour/minute 為單一整數的工作（哨兵是範圍，略過）。"""
    out: dict[str, int] = {}
    for job in scheduler.get_jobs():
        parts = {f.name: str(f) for f in job.trigger.fields}
        try:
            out[job.args[0]] = int(parts["hour"]) * 60 + int(parts["minute"])
        except ValueError:
            continue  # 範圍型（如 hour='9-13'）不參與順序檢查
    return out


async def test_daily_sequences_generate_overview_after_batch_analysis():
    """晨間序列的相依順序：批次分析 → 簡報 → 產生委託。
    （遷移 Zeabur 後改由後端排程器負責，先前是 GitHub Actions 的 cron 序列）"""
    from app.scheduler.jobs import start_scheduler

    scheduler = start_scheduler()
    try:
        at = _scheduled_minutes(scheduler)
        for market in ("tw", "us"):
            assert at[f"ai-batch-{market}"] < at[f"overview-{market}"] < at[f"sim-decide-{market}"], (
                f"{market} 晨間序列順序錯誤：{at}"
            )
        # 收盤後同步必須早於撮合（撮合要吃到當日開盤價）
        for market in ("tw", "us"):
            assert at[f"sync-{market}"] < at[f"sim-fill-{market}"]
    finally:
        scheduler.shutdown(wait=False)
