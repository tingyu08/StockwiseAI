from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import AppError
from app.models import AiOverview, AiReport, DailyPrice, EtfNav, JobRun, Stock
from app.services.premium_service import SUPPORTED_MARKETS as PREMIUM_MARKETS
from app.services.time_service import as_utc_iso

router = APIRouter(tags=["health"])


class ReadinessError(AppError):
    status_code = 503


@router.api_route("/health", methods=["GET", "HEAD"], response_model=Envelope)
def health() -> Envelope:
    return ok({"status": "ok"})


# UptimeRobot 等監測服務預設用 HEAD 探測，需一併支援（否則 405 → 誤判 Down）
@router.api_route("/health/live", methods=["GET", "HEAD"], response_model=Envelope)
def liveness() -> Envelope:
    return ok({"status": "alive"})


@router.get("/health/ready", response_model=Envelope)
def readiness(db: Session = Depends(get_db)) -> Envelope:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ReadinessError("資料庫目前無法使用") from exc
    return ok({"status": "ready", "database": "ok"})


@router.get("/data-status", response_model=Envelope)
def data_status(db: Session = Depends(get_db)) -> Envelope:
    result = {}
    for market in ("TW", "US"):
        latest_price = db.execute(
            select(func.max(DailyPrice.date))
            .join(Stock, DailyPrice.stock_id == Stock.id)
            .where(Stock.market == market)
        ).scalar_one_or_none()
        # 不支援折溢價的市場一律回 None：功能下架後 etf_nav 仍留著舊資料，
        # 無條件查最大日期會把「已停止更新的歷史殘留」當成當前資料新鮮度回報
        # （美股淨值停在下架前那天，看起來就像排程壞了）。
        latest_nav = (
            db.execute(
                select(func.max(EtfNav.date))
                .join(Stock, EtfNav.stock_id == Stock.id)
                .where(Stock.market == market)
            ).scalar_one_or_none()
            if market in PREMIUM_MARKETS
            else None
        )
        # 取「最後產生時間」而非 trade_date。trade_date 是這份分析所根據的
        # 收盤日，美股資料鏈天生落後一個 session，於是畫面上會出現
        # 「例行 07-24」但工作其實是 07-27 跑的——看起來像排程停擺。
        # 使用者要看的是「上次跑是什麼時候」，那是 created_at。
        ai_rows = db.execute(
            select(AiReport.kind, func.max(AiReport.created_at))
            .join(Stock, AiReport.stock_id == Stock.id)
            .where(Stock.market == market)
            .group_by(AiReport.kind)
        ).all()
        ai_runs = {kind: ran_at for kind, ran_at in ai_rows}
        latest_job = db.execute(
            select(JobRun)
            .where(
                JobRun.status == "succeeded",
                JobRun.name.ilike(f"%-{market.lower()}"),
            )
            .order_by(JobRun.finished_at.desc(), JobRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        overview_run = db.execute(
            select(func.max(AiOverview.created_at)).where(AiOverview.market == market)
        ).scalar_one_or_none()
        result[market] = {
            # 行情/NAV 仍是資料日期：它們回答「我手上的資料到哪一天」，
            # 換成「同步幾點跑的」反而失去意義（答案永遠是今天）。
            "latest_price_date": latest_price.isoformat() if latest_price else None,
            "latest_nav_date": latest_nav.isoformat() if latest_nav else None,
            # 以下皆為「上次執行時間」（UTC，帶時區標記供前端轉當地時間）
            "latest_ai_runs": {
                kind: as_utc_iso(ai_runs.get(kind))
                for kind in ("news", "routine", "trade")
            },
            "latest_overview_run": as_utc_iso(overview_run),
            "latest_successful_job": {
                "id": latest_job.id,
                "name": latest_job.name,
                "finished_at": as_utc_iso(latest_job.finished_at),
            }
            if latest_job
            else None,
        }
    return ok(result)
