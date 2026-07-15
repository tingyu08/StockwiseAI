import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    alerts, analysis, auth, backtest, compare, health, jobs, predictions, premium,
    simulation, stocks, usage, watchlist,
)
from app.core.config import get_settings
from app.core.auth import require_login
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import configure_sensitive_logging
from app.core.request_timing import install_db_timing, request_timing_middleware

logging.basicConfig(level=logging.INFO)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


async def add_security_headers(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if get_settings().environment == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.job_service import run_worker_loop

    scheduler = None
    worker_task = asyncio.create_task(run_worker_loop())
    if get_settings().scheduler_mode == "internal":
        from app.scheduler.jobs import start_scheduler

        scheduler = start_scheduler()
    yield
    worker_task.cancel()
    with suppress(asyncio.CancelledError):
        await worker_task
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()  # fail fast：缺必填環境變數這裡就會炸
    configure_sensitive_logging(settings)
    install_db_timing()
    app = FastAPI(title="stock-ai-advisor", version="0.1.0", lifespan=lifespan)

    app.middleware("http")(add_security_headers)
    app.middleware("http")(require_login)
    app.middleware("http")(request_timing_middleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    for router in (
        health.router, auth.router, usage.router, stocks.router, watchlist.router,
        jobs.router, analysis.router, compare.router, premium.router, predictions.router,
        simulation.router, alerts.router, backtest.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
