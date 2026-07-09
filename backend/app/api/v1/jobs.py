"""排程觸發 API — 方案 B（external 模式）由 GitHub Actions cron 呼叫。"""
import asyncio
import hmac
import logging

from fastapi import APIRouter, Header

from app.core.config import get_settings
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError, NotFoundError
from app.scheduler.jobs import JOBS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

# Render 免費層 LB 約 100 秒切斷回應；新聞研究一檔 1~3 分鐘，
# 改為觸發即返回、背景執行（呼叫端需持續 ping /health 保持實例清醒）
BACKGROUND_JOBS = {"news-tw", "news-us"}


class UnauthorizedError(AppError):
    status_code = 401


async def _run_logged(name: str, job) -> None:
    try:
        result = await job()
        logger.info("background job %s done: %s", name, result)
    except Exception:
        logger.exception("background job %s failed", name)


@router.post("/jobs/{name}:run", response_model=Envelope)
async def run_job(name: str, x_job_token: str = Header(default="")) -> Envelope:
    settings = get_settings()
    if not settings.job_token or not hmac.compare_digest(x_job_token, settings.job_token):
        raise UnauthorizedError("JOB_TOKEN 驗證失敗")
    job = JOBS.get(name)
    if job is None:
        raise NotFoundError(f"查無排程：{name}（可用：{', '.join(JOBS)}）")
    if name in BACKGROUND_JOBS:
        asyncio.create_task(_run_logged(name, job))
        return ok({"started": True, "job": name})
    result = await job()
    return ok(result)
