"""排程觸發 API — 方案 B（external 模式）由 GitHub Actions cron 呼叫。"""
import hmac

from fastapi import APIRouter, Header

from app.core.config import get_settings
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError, NotFoundError
from app.scheduler.jobs import JOBS

router = APIRouter(tags=["jobs"])


class UnauthorizedError(AppError):
    status_code = 401


@router.post("/jobs/{name}:run", response_model=Envelope)
async def run_job(name: str, x_job_token: str = Header(default="")) -> Envelope:
    settings = get_settings()
    if not settings.job_token or not hmac.compare_digest(x_job_token, settings.job_token):
        raise UnauthorizedError("JOB_TOKEN 驗證失敗")
    job = JOBS.get(name)
    if job is None:
        raise NotFoundError(f"查無排程：{name}（可用：{', '.join(JOBS)}）")
    result = await job()
    return ok(result)
