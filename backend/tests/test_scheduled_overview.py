from pathlib import Path

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


def test_github_daily_sequences_generate_overview_after_batch_analysis():
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "cron.yml").read_text(
        encoding="utf-8"
    )

    assert "ai-batch-tw overview-tw sim-decide-tw" in workflow
    assert "ai-batch-us overview-us sim-decide-us" in workflow
