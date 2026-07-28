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
    """AI 額度不足。

    scope 是給呼叫端判斷「該放棄還是該等一下」用的，缺了它會出事：
    RPD 用盡代表今天沒了（批次工作應收工），但 RPM/TPM 只是這一分鐘滿了，
    等數十秒就恢復。兩者若都只看得到一個 QuotaExceededError，批次工作會把
    「這一分鐘滿了」當成「今天沒了」而放棄整批剩餘工作。

    - rpd      今日請求數用盡 → 放棄
    - rpm/tpm  當下這一分鐘的請求數／token 數滿了 → 稍候重試
    - upstream 上游（Google）回 429 → 無從得知是哪一種，保守放棄
    - config   quotas.yaml 沒有這個模型 → 設定錯誤，不該被當成額度用盡吞掉
    """

    status_code = 429
    RETRYABLE_SCOPES = ("rpm", "tpm")

    def __init__(self, message: str, scope: str = "rpd"):
        self.scope = scope
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        """等一下就會恢復（分鐘級視窗），而非今日已無額度。"""
        return self.scope in self.RETRYABLE_SCOPES


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
