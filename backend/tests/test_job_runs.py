import json

from app.api.v1 import jobs
from app.core.db import SessionLocal


async def test_job_execution_persists_success_result(client):
    create_run = getattr(jobs, "create_job_run", None)
    execute_run = getattr(jobs, "execute_job_run", None)
    assert callable(create_run)
    assert callable(execute_run)

    run_id = create_run("test-success")

    async def succeeds():
        return {"updated": 3}

    await execute_run(run_id, succeeds)

    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "succeeded"
        assert run.attempts == 1
        assert json.loads(run.result_json) == {"updated": 3}
        assert run.finished_at is not None
    finally:
        db.close()


async def test_job_execution_persists_failure_for_retry(client):
    create_run = getattr(jobs, "create_job_run", None)
    execute_run = getattr(jobs, "execute_job_run", None)
    assert callable(create_run)
    assert callable(execute_run)

    run_id = create_run("test-failure")

    async def fails():
        raise RuntimeError("provider unavailable")

    await execute_run(run_id, fails)

    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "failed"
        assert run.attempts == 1
        assert "provider unavailable" in run.error
    finally:
        db.close()
