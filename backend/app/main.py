import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    analysis, compare, health, jobs, predictions, premium, stocks, usage, watchlist,
)
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if get_settings().scheduler_mode == "internal":
        from app.scheduler.jobs import start_scheduler

        scheduler = start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()  # fail fast：缺必填環境變數這裡就會炸
    app = FastAPI(title="stock-ai-advisor", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins.split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    for router in (
        health.router, usage.router, stocks.router, watchlist.router,
        jobs.router, analysis.router, compare.router, premium.router, predictions.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
