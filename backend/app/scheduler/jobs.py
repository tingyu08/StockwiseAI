"""每日排程：收盤後同步自選股資料。

internal 模式（本機/Zeabur）：APScheduler 直接跑。
external 模式（Render＋GitHub Actions）：由 POST /jobs/{name}:run 觸發同名函式。
時間皆為台灣時間（Asia/Taipei）。
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Stock, WatchlistItem
from app.services.sync_service import sync_prices

logger = logging.getLogger(__name__)

TZ = "Asia/Taipei"


async def sync_market_daily(market: str) -> dict:
    """同步該市場所有自選股。單檔失敗不中斷其他檔。"""
    db = SessionLocal()
    synced, failed = 0, []
    try:
        stocks = db.execute(
            select(Stock)
            .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
            .where(Stock.market == market)
        ).scalars().all()
        for stock in stocks:
            try:
                await sync_prices(db, stock)
                synced += 1
            except Exception:
                logger.exception("sync %s/%s failed", market, stock.symbol)
                failed.append(stock.symbol)
        return {"market": market, "synced": synced, "failed": failed}
    finally:
        db.close()  # Neon 紀律：job 結束即釋放連線


async def ai_batch_daily(market: str) -> dict:
    """對該市場 AI 託管清單跑例行批次分析（flash-lite 降級鏈）。"""
    from app.services.analysis_service import run_batch

    db = SessionLocal()
    try:
        stocks = db.execute(
            select(Stock)
            .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
            .where(Stock.market == market, WatchlistItem.ai_managed.is_(True))
        ).scalars().all()
        if not stocks:
            return {"market": market, "analyzed": 0, "note": "無 AI 託管股票"}
        result = await run_batch(db, stocks)
        return {"market": market, **result}
    finally:
        db.close()  # Neon 紀律：job 結束即釋放連線


async def nav_snapshot_daily(market: str) -> dict:
    """ETF 淨值/折溢價每日快照。"""
    from app.services.premium_service import snapshot_premiums

    db = SessionLocal()
    try:
        return await snapshot_premiums(db, market)
    finally:
        db.close()


JOBS = {
    "sync-tw": lambda: sync_market_daily("TW"),
    "sync-us": lambda: sync_market_daily("US"),
    "ai-batch-tw": lambda: ai_batch_daily("TW"),
    "ai-batch-us": lambda: ai_batch_daily("US"),
    "nav-tw": lambda: nav_snapshot_daily("TW"),
    "nav-us": lambda: nav_snapshot_daily("US"),
}


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)
    # 台股：14:30 同步 → 15:00 AI 批次；美股（台灣時間）：05:30 同步 → 06:00 AI 批次
    scheduler.add_job(sync_market_daily, CronTrigger(hour=14, minute=30, timezone=TZ), args=["TW"])
    scheduler.add_job(nav_snapshot_daily, CronTrigger(hour=14, minute=45, timezone=TZ), args=["TW"])
    scheduler.add_job(ai_batch_daily, CronTrigger(hour=15, minute=0, timezone=TZ), args=["TW"])
    scheduler.add_job(sync_market_daily, CronTrigger(hour=5, minute=30, timezone=TZ), args=["US"])
    scheduler.add_job(nav_snapshot_daily, CronTrigger(hour=5, minute=45, timezone=TZ), args=["US"])
    scheduler.add_job(ai_batch_daily, CronTrigger(hour=6, minute=0, timezone=TZ), args=["US"])
    scheduler.start()
    logger.info("APScheduler started (internal mode)")
    return scheduler
