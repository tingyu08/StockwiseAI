"""Externally triggered jobs with durable execution history and retry support."""

import hmac
import json
import logging

from fastapi import APIRouter, Header

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError, NotFoundError
from app.models import JobRun
from app.scheduler.jobs import JOBS
from app.services.job_service import enqueue_job, retry_job

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


class UnauthorizedError(AppError):
    status_code = 401


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
    retry_job(run_id)
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        return ok({"started": True, "job": run.name, "run_id": run_id})
    finally:
        db.close()


@router.post("/jobs/{name}:run", response_model=Envelope)
async def run_job(name: str, x_job_token: str = Header(default="")) -> Envelope:
    settings = get_settings()
    if not settings.job_token or not hmac.compare_digest(x_job_token, settings.job_token):
        raise UnauthorizedError("JOB_TOKEN 驗證失敗")
    job = JOBS.get(name)
    if job is None:
        raise NotFoundError(f"查無排程：{name}（可用：{', '.join(JOBS)}）")

    run_id = enqueue_job(
        name,
        job_type="scheduled",
        payload={"name": name},
        idempotency_key=f"scheduled:{name}",
    )
    return ok({"started": True, "job": name, "run_id": run_id})
