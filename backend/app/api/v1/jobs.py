"""Externally triggered jobs with durable execution history and retry support."""

import asyncio
import hmac
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError, NotFoundError
from app.models import JobRun
from app.scheduler.jobs import JOBS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])
BACKGROUND_JOBS = {"news-tw", "news-us"}


class UnauthorizedError(AppError):
    status_code = 401


class JobFailedError(AppError):
    status_code = 502


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_job_run(name: str) -> int:
    db = SessionLocal()
    try:
        run = JobRun(name=name, status="queued")
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


async def execute_job_run(run_id: int, job) -> dict | None:
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is None:
            raise NotFoundError(f"查無工作紀錄：{run_id}")
        run.status = "running"
        run.attempts += 1
        run.started_at = _utc_now()
        run.finished_at = None
        run.error = None
        db.commit()
    finally:
        db.close()

    try:
        result = await job()
    except Exception as exc:
        logger.exception("job run %s failed", run_id)
        db = SessionLocal()
        try:
            run = db.get(JobRun, run_id)
            run.status = "failed"
            run.error = str(exc)[:4000]
            run.finished_at = _utc_now()
            db.commit()
        finally:
            db.close()
        return None

    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        run.status = "succeeded"
        run.result_json = json.dumps(result, ensure_ascii=False, default=str)
        run.finished_at = _utc_now()
        db.commit()
    finally:
        db.close()
    return result


def _run_dto(run: JobRun) -> dict:
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "attempts": run.attempts,
        "result": json.loads(run.result_json) if run.result_json else None,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/jobs/runs/{run_id}", response_model=Envelope)
def get_job_run(run_id: int) -> Envelope:
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is None:
            raise NotFoundError(f"查無工作紀錄：{run_id}")
        return ok(_run_dto(run))
    finally:
        db.close()


@router.post("/jobs/runs/{run_id}:retry", response_model=Envelope)
async def retry_job_run(run_id: int) -> Envelope:
    db = SessionLocal()
    try:
        prior = db.get(JobRun, run_id)
        if prior is None:
            raise NotFoundError(f"查無工作紀錄：{run_id}")
        if prior.status != "failed":
            raise JobFailedError("只有失敗的工作可以重試")
        name = prior.name
    finally:
        db.close()
    job = JOBS.get(name)
    if job is None:
        raise NotFoundError(f"排程已不存在：{name}")
    new_id = create_job_run(name)
    asyncio.create_task(execute_job_run(new_id, job))
    return ok({"started": True, "job": name, "run_id": new_id})


@router.post("/jobs/{name}:run", response_model=Envelope)
async def run_job(name: str, x_job_token: str = Header(default="")) -> Envelope:
    settings = get_settings()
    if not settings.job_token or not hmac.compare_digest(x_job_token, settings.job_token):
        raise UnauthorizedError("JOB_TOKEN 驗證失敗")
    job = JOBS.get(name)
    if job is None:
        raise NotFoundError(f"查無排程：{name}（可用：{', '.join(JOBS)}）")

    run_id = create_job_run(name)
    if name in BACKGROUND_JOBS:
        asyncio.create_task(execute_job_run(run_id, job))
        return ok({"started": True, "job": name, "run_id": run_id})

    result = await execute_job_run(run_id, job)
    if result is None:
        raise JobFailedError(f"排程執行失敗，run_id={run_id}")
    return ok({"run_id": run_id, **result})
