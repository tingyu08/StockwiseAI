"""Database-backed job queue primitives shared by API and worker execution."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.core.exceptions import AppError, NotFoundError
from app.models import JobRun

DEFAULT_LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
STALE_SWEEP_SECONDS = 30
logger = logging.getLogger(__name__)
Dispatcher = Callable[[str, dict], Awaitable[dict | None]]


class JobStateError(AppError):
    status_code = 409


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_job(
    name: str,
    *,
    job_type: str = "scheduled",
    payload: dict | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> int:
    db = SessionLocal()
    try:
        if idempotency_key:
            active = db.execute(
                select(JobRun).where(
                    JobRun.idempotency_key == idempotency_key,
                    JobRun.status.in_(("queued", "running")),
                )
            ).scalar_one_or_none()
            if active:
                return active.id
        run = JobRun(
            name=name,
            job_type=job_type,
            payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            idempotency_key=idempotency_key,
            status="queued",
            max_attempts=max(1, max_attempts),
        )
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if not idempotency_key:
                raise
            active = db.execute(
                select(JobRun).where(
                    JobRun.idempotency_key == idempotency_key,
                    JobRun.status.in_(("queued", "running")),
                )
            ).scalar_one()
            return active.id
        db.refresh(run)
        return run.id
    finally:
        db.close()


def recover_stale_jobs(now: datetime | None = None) -> int:
    """Requeue expired leases; permanently fail jobs that exhausted attempts."""
    now = now or utc_now()
    db = SessionLocal()
    recovered = 0
    try:
        runs = db.execute(
            select(JobRun).where(
                JobRun.status == "running",
                JobRun.lease_expires_at.is_not(None),
                JobRun.lease_expires_at < now,
            )
        ).scalars().all()
        for run in runs:
            recovered += 1
            run.heartbeat_at = None
            run.lease_expires_at = None
            if run.attempts < run.max_attempts:
                run.status = "queued"
                run.error = "工作執行程序中斷，已重新排隊"
            else:
                run.status = "failed"
                run.error = "工作執行程序中斷，且已達最大重試次數"
                run.finished_at = now
        db.commit()
        return recovered
    finally:
        db.close()


def claim_next_job(now: datetime | None = None) -> int | None:
    """Claim the oldest queued job and grant a renewable execution lease."""
    now = now or utc_now()
    db = SessionLocal()
    try:
        stmt = select(JobRun).where(JobRun.status == "queued").order_by(JobRun.created_at, JobRun.id)
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        else:
            stmt = stmt.with_for_update()
        run = db.execute(stmt.limit(1)).scalar_one_or_none()
        if run is None:
            return None
        run.status = "running"
        run.attempts += 1
        run.started_at = now
        run.finished_at = None
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
        run.error = None
        db.commit()
        return run.id
    finally:
        db.close()


def retry_job(run_id: int) -> int:
    """Requeue a failed job in place so its type and payload remain retryable."""
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is None:
            raise NotFoundError(f"查無工作紀錄：{run_id}")
        if run.status != "failed":
            raise JobStateError("只有失敗的工作可以重試")
        run.max_attempts = max(run.max_attempts, run.attempts + 1)
        run.status = "queued"
        run.error = None
        run.started_at = None
        run.finished_at = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        db.commit()
        return run.id
    finally:
        db.close()


def heartbeat_job(run_id: int, now: datetime | None = None) -> bool:
    now = now or utc_now()
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is None or run.status != "running":
            return False
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
        db.commit()
        return True
    finally:
        db.close()


async def dispatch_job(job_type: str, payload: dict) -> dict | None:
    if job_type == "scheduled":
        from app.scheduler.jobs import JOBS

        name = payload.get("name")
        job = JOBS.get(name)
        if job is None:
            raise NotFoundError(f"查無排程：{name}")
        return await job()

    if job_type == "overview":
        from app.services import analysis_service

        market = payload["market"]
        db = SessionLocal()
        try:
            overview = await analysis_service.run_overview(
                db, market, force=bool(payload.get("force", False))
            )
            return analysis_service.overview_dto(overview)
        finally:
            db.close()

    if job_type == "news":
        from sqlalchemy import select as sa_select

        from app.models import Stock
        from app.services import news_service

        market, symbol = payload["market"], payload["symbol"]
        db = SessionLocal()
        try:
            stock = db.execute(
                sa_select(Stock).where(
                    Stock.market == market, Stock.symbol == symbol
                )
            ).scalar_one_or_none()
            if stock is None:
                raise NotFoundError(f"尚未追蹤 {market}/{symbol}")
            report = await news_service.run_news_research(
                db, stock, force=bool(payload.get("force", False))
            )
            return news_service.news_dto(report)
        finally:
            db.close()

    if job_type == "stock_sync":
        from sqlalchemy import select as sa_select

        from app.models import Stock
        from app.services.sync_service import sync_prices

        market, symbol = payload["market"], payload["symbol"]
        db = SessionLocal()
        try:
            stock = db.execute(
                sa_select(Stock).where(Stock.market == market, Stock.symbol == symbol)
            ).scalar_one_or_none()
            if stock is None:
                raise NotFoundError(f"尚未追蹤 {market}/{symbol}")
            stock_id = stock.id
        finally:
            db.close()
        changed = await sync_prices(stock_id, market, symbol)
        return {"market": market, "symbol": symbol, "synced_rows": changed}

    if job_type == "simulation_decide":
        from app.scheduler.jobs import sim_decide_daily

        return await sim_decide_daily(payload["market"])

    raise NotFoundError(f"未知工作類型：{job_type}")


async def _heartbeat_loop(run_id: int) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if not heartbeat_job(run_id):
            return


def _finish_job(
    run_id: int, *, result: dict | None = None, error: Exception | None = None
) -> None:
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is None:
            return
        now = utc_now()
        run.heartbeat_at = None
        run.lease_expires_at = None
        if error is None:
            run.status = "succeeded"
            run.result_json = json.dumps(result, ensure_ascii=False, default=str)
            run.error = None
            run.finished_at = now
        elif run.attempts < run.max_attempts:
            run.status = "queued"
            run.error = str(error)[:4000]
        else:
            run.status = "failed"
            run.error = str(error)[:4000]
            run.finished_at = now
        db.commit()
    finally:
        db.close()


async def execute_claimed_job(
    run_id: int, *, dispatcher: Dispatcher = dispatch_job
) -> dict | None:
    db = SessionLocal()
    try:
        run = db.get(JobRun, run_id)
        if run is None:
            raise NotFoundError(f"查無工作紀錄：{run_id}")
        if run.status != "running":
            raise JobStateError("只有 running 工作可以執行")
        job_type = run.job_type
        payload = json.loads(run.payload_json or "{}")
    finally:
        db.close()

    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))
    try:
        result = await dispatcher(job_type, payload)
    except Exception as exc:
        logger.exception("job run %s failed", run_id)
        _finish_job(run_id, error=exc)
        return None
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    _finish_job(run_id, result=result)
    return result


async def run_worker_loop(poll_interval: float = 1.0) -> None:
    """Continuously recover/claim jobs. Safe to run in multiple processes.

    迴圈本體整段包例外處理：本 task 由 lifespan 以 create_task 建立後無人
    await，任何逸出的例外都會靜默終結 worker——排程仍持續入列卻再也沒人
    執行（單機部署等同全站排程停擺且毫無錯誤訊息）。暫時性 DB 故障
    （連線被切、冷啟逾時）只該損失一輪，不該讓 worker 永久死亡。

    stale lease 掃描改為固定週期而非每輪：閒置時原本每秒一次 SELECT，
    一天下來近 9 萬次無謂查詢。
    """
    last_sweep = float("-inf")
    while True:
        try:
            now = asyncio.get_running_loop().time()
            if now - last_sweep >= STALE_SWEEP_SECONDS:
                recover_stale_jobs()
                last_sweep = now
            run_id = claim_next_job()
            if run_id is None:
                await asyncio.sleep(poll_interval)
                continue
            await execute_claimed_job(run_id)
        except asyncio.CancelledError:
            raise  # 關機時的正常取消，必須往外傳
        except Exception:
            logger.exception("worker loop iteration failed; continuing")
            await asyncio.sleep(poll_interval)
