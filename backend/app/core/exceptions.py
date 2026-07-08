"""Domain exceptions mapped to HTTP responses with the unified envelope."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.envelope import fail

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base domain error. `message` is user-facing; log details separately."""

    status_code = 500

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    status_code = 404


class QuotaExceededError(AppError):
    status_code = 429


class UpstreamError(AppError):
    """External data source / AI provider failure."""

    status_code = 502


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("AppError on %s %s: %s", request.method, request.url.path, exc.message)
        return JSONResponse(
            status_code=exc.status_code, content=fail(exc.message).model_dump()
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content=fail("輸入格式錯誤").model_dump())

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=fail("伺服器內部錯誤").model_dump())
