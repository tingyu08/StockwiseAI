import json
from datetime import datetime, timedelta, timezone

import app.services as services
from app.api.v1 import jobs
from app.core.db import SessionLocal


def test_enqueue_job_is_idempotent_while_active():
    job_service = getattr(services, "job_service", None)
    assert job_service is not None
    if job_service is None:
        return

    first = job_service.enqueue_job(
        "news-tw-2330",
        job_type="news",
        payload={"market": "TW", "symbol": "2330"},
        idempotency_key="news:TW:2330:2026-07-10",
    )
    second = job_service.enqueue_job(
        "news-tw-2330",
        job_type="news",
        payload={"market": "TW", "symbol": "2330"},
        idempotency_key="news:TW:2330:2026-07-10",
    )

    assert second == first
    db = SessionLocal()
    try:
        db.delete(db.get(jobs.JobRun, first))
        db.commit()
    finally:
        db.close()


def test_recover_stale_job_requeues_before_max_attempts():
    job_service = getattr(services, "job_service", None)
    assert job_service is not None
    if job_service is None:
        return

    run_id = job_service.enqueue_job(
        "overview-tw",
        job_type="overview",
        payload={"market": "TW"},
        idempotency_key="overview:TW:stale-test",
    )
    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        run.status = "running"
        run.attempts = 1
        run.max_attempts = 3
        run.lease_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        )
        db.commit()
    finally:
        db.close()

    assert job_service.recover_stale_jobs() == 1
    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "queued"
        assert run.error == "工作執行程序中斷，已重新排隊"
    finally:
        db.delete(run)
        db.commit()
        db.close()


def test_claim_next_job_sets_lease_and_attempt():
    job_service = services.job_service
    run_id = job_service.enqueue_job(
        "claim-test", job_type="scheduled", payload={"name": "sync-tw"}
    )

    claimed = getattr(job_service, "claim_next_job", lambda: None)()

    assert claimed == run_id
    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "running"
        assert run.attempts == 1
        assert run.heartbeat_at is not None
        assert run.lease_expires_at > run.heartbeat_at
        db.delete(run)
        db.commit()
    finally:
        db.close()


def test_dynamic_failed_job_can_be_retried(client):
    run_id = services.job_service.enqueue_job(
        "news-tw-2330",
        job_type="news",
        payload={"market": "TW", "symbol": "2330"},
    )
    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        run.status = "failed"
        run.attempts = 3
        run.max_attempts = 3
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/jobs/runs/{run_id}:retry")

    assert response.status_code == 200
    assert response.json()["data"]["run_id"] == run_id
    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "queued"
        assert json.loads(run.payload_json) == {"market": "TW", "symbol": "2330"}
        assert run.max_attempts == 4
        db.delete(run)
        db.commit()
    finally:
        db.close()


async def test_worker_executes_claimed_payload_and_persists_result():
    job_service = services.job_service
    run_id = job_service.enqueue_job(
        "payload-test", job_type="test", payload={"value": 7}
    )
    assert job_service.claim_next_job() == run_id

    seen = {}

    async def dispatcher(job_type, payload):
        seen.update({"job_type": job_type, "payload": payload})
        return {"doubled": payload["value"] * 2}

    execute = getattr(job_service, "execute_claimed_job", None)
    assert callable(execute)
    if not execute:
        return
    await execute(run_id, dispatcher=dispatcher)

    assert seen == {"job_type": "test", "payload": {"value": 7}}
    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "succeeded"
        assert json.loads(run.result_json) == {"doubled": 14}
        assert run.heartbeat_at is None
        assert run.lease_expires_at is None
        db.delete(run)
        db.commit()
    finally:
        db.close()


async def test_worker_requeues_transient_failure_before_max_attempts():
    job_service = services.job_service
    run_id = job_service.enqueue_job(
        "failure-test", job_type="test", payload={}, max_attempts=2
    )
    assert job_service.claim_next_job() == run_id

    async def fails(job_type, payload):
        raise RuntimeError("provider unavailable")

    await job_service.execute_claimed_job(run_id, dispatcher=fails)

    db = SessionLocal()
    try:
        run = db.get(jobs.JobRun, run_id)
        assert run.status == "queued"
        assert run.attempts == 1
        assert "provider unavailable" in run.error
        db.delete(run)
        db.commit()
    finally:
        db.close()
