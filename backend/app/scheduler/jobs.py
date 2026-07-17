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
from app.services.time_service import market_today
from app.services.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

TZ = "Asia/Taipei"


def _non_trading_gate(market: str) -> dict | None:
    """假日/週末閘門：非交易日整段跳過（不喚醒外部 API、不燒 AI 額度）。

    US 排程於台灣清晨執行時，market_today("US") 即為剛收盤的美東日期，
    直接以該日判斷是否為交易日即可。
    """
    today = market_today(market)
    if is_trading_day(market, today):
        return None
    logger.info("%s %s 非交易日，排程跳過", market, today)
    return {"market": market, "skipped": f"{today} 非交易日"}


async def sync_market_daily(market: str) -> dict:
    """同步該市場所有自選股。單檔失敗不中斷其他檔。"""
    if gate := _non_trading_gate(market):
        return gate
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
                await sync_prices(stock.id, stock.market, stock.symbol)
                synced += 1
            except Exception:
                logger.exception("sync %s/%s failed", market, stock.symbol)
                failed.append(stock.symbol)
        return {"market": market, "synced": synced, "failed": failed}
    finally:
        db.close()  # Neon 紀律：job 結束即釋放連線


async def ai_batch_daily(market: str) -> dict:
    """對該市場 AI 託管清單跑例行批次分析（flash-lite 降級鏈）。"""
    if gate := _non_trading_gate(market):
        return gate
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


async def overview_daily(market: str) -> dict:
    """Generate the cached four-module daily investment briefing."""
    if gate := _non_trading_gate(market):
        return gate
    from app.services.analysis_service import overview_dto, run_overview

    db = SessionLocal()
    try:
        overview = await run_overview(db, market)
        return overview_dto(overview)
    finally:
        db.close()


async def news_research_daily(market: str) -> dict:
    """AI 託管清單的每日新聞研究（Antigravity），於例行批次分析前執行。

    單檔失敗不中斷（agent 任務較不穩定）；額度盡即提前收工，
    已完成的摘要仍會被當日批次分析吃到。
    """
    if gate := _non_trading_gate(market):
        return gate
    from app.core.exceptions import QuotaExceededError
    from app.services.news_service import run_news_research

    db = SessionLocal()
    researched, failed = 0, []
    try:
        stocks = db.execute(
            select(Stock)
            .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
            .where(Stock.market == market, WatchlistItem.ai_managed.is_(True))
        ).scalars().all()
        for stock in stocks:
            try:
                await run_news_research(db, stock)
                researched += 1
            except QuotaExceededError:
                logger.warning("Antigravity 額度已盡，%s 新聞研究提前結束", market)
                break
            except Exception:
                logger.exception("news research %s/%s failed", market, stock.symbol)
                failed.append(stock.symbol)
        return {"market": market, "researched": researched, "failed": failed}
    finally:
        db.close()  # Neon 紀律：job 結束即釋放連線


async def sim_decide_daily(market: str) -> dict:
    """以 3.5 優先的交易分析產生模擬委託單（隔日開盤成交）。"""
    if gate := _non_trading_gate(market):
        return gate
    from app.services.analysis_service import run_batch
    from app.services.sim.decision import run_decisions

    db = SessionLocal()
    try:
        stocks = db.execute(
            select(Stock)
            .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
            .where(Stock.market == market, WatchlistItem.ai_managed.is_(True))
        ).scalars().all()
        batch = (
            await run_batch(db, stocks, kind="trade")
            if stocks
            else {"analyzed": 0, "skipped": 0, "model": None}
        )
        return {**run_decisions(db, market), "batch": batch}
    finally:
        db.close()


async def sim_fill_daily(market: str) -> dict:
    """資料同步後撮合 pending 單。"""
    if gate := _non_trading_gate(market):
        return gate
    from app.services.sim.engine import fill_pending_orders

    db = SessionLocal()
    try:
        return fill_pending_orders(db, market)
    finally:
        db.close()


async def nav_snapshot_daily(market: str) -> dict:
    """ETF 淨值/折溢價每日快照。"""
    if gate := _non_trading_gate(market):
        return gate
    from app.services.premium_service import snapshot_premiums

    db = SessionLocal()
    try:
        return await snapshot_premiums(db, market)
    finally:
        db.close()


async def alert_check_daily(market: str) -> dict:
    """價格/折溢價警示檢查（於同步與淨值快照之後）。"""
    if gate := _non_trading_gate(market):
        return gate
    from app.services.alert_service import check_alerts, deliver_pending_notifications

    db = SessionLocal()
    try:
        result = check_alerts(db, market)
        notifications = await deliver_pending_notifications(db)
    finally:
        db.close()
    return {**result, "notifications": notifications}


async def exit_sentinel_job(market: str) -> dict:
    """盤中出場哨兵：持倉的停損/停利即時檢查（零 AI 呼叫，非交易時段自動 no-op）。"""
    from app.services.sim.sentinel import run_exit_sentinel

    db = SessionLocal()
    try:
        return await run_exit_sentinel(db, market)
    finally:
        db.close()


async def maintenance_daily() -> dict:
    """Apply bounded retention to successful jobs, failures, and AI usage logs."""
    from app.services.maintenance_service import cleanup_expired_records

    db = SessionLocal()
    try:
        return cleanup_expired_records(db)
    finally:
        db.close()


JOBS = {
    "sync-tw": lambda: sync_market_daily("TW"),
    "sync-us": lambda: sync_market_daily("US"),
    "news-tw": lambda: news_research_daily("TW"),
    "news-us": lambda: news_research_daily("US"),
    "ai-batch-tw": lambda: ai_batch_daily("TW"),
    "ai-batch-us": lambda: ai_batch_daily("US"),
    "overview-tw": lambda: overview_daily("TW"),
    "overview-us": lambda: overview_daily("US"),
    "nav-tw": lambda: nav_snapshot_daily("TW"),
    "nav-us": lambda: nav_snapshot_daily("US"),
    "sim-decide-tw": lambda: sim_decide_daily("TW"),
    "sim-decide-us": lambda: sim_decide_daily("US"),
    "sim-fill-tw": lambda: sim_fill_daily("TW"),
    "sim-fill-us": lambda: sim_fill_daily("US"),
    "alerts-tw": lambda: alert_check_daily("TW"),
    "alerts-us": lambda: alert_check_daily("US"),
    "sentinel-tw": lambda: exit_sentinel_job("TW"),
    "sentinel-us": lambda: exit_sentinel_job("US"),
    "maintenance": maintenance_daily,
}


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)
    # 分析/決策＝開盤前晨間（已消化昨收＋隔夜美股/國際盤）；成交於當日開盤價。
    # 資料任務（同步/撮合/淨值/警示）＝收盤後。
    #
    # 台股晨間：06:10 新聞 → 06:40 AI 批次 → 06:55 簡報 → 07:10 產生委託（09:00 開盤成交）
    scheduler.add_job(news_research_daily, CronTrigger(hour=6, minute=10, timezone=TZ), args=["TW"])
    scheduler.add_job(ai_batch_daily, CronTrigger(hour=6, minute=40, timezone=TZ), args=["TW"])
    scheduler.add_job(overview_daily, CronTrigger(hour=6, minute=55, timezone=TZ), args=["TW"])
    scheduler.add_job(sim_decide_daily, CronTrigger(hour=7, minute=10, timezone=TZ), args=["TW"])
    # 台股收盤後：14:30 同步 → 14:35 撮合晨間委託（記入今日開盤價）→ 14:45 淨值 → 14:50 警示
    scheduler.add_job(sync_market_daily, CronTrigger(hour=14, minute=30, timezone=TZ), args=["TW"])
    scheduler.add_job(sim_fill_daily, CronTrigger(hour=14, minute=35, timezone=TZ), args=["TW"])
    scheduler.add_job(nav_snapshot_daily, CronTrigger(hour=14, minute=45, timezone=TZ), args=["TW"])
    scheduler.add_job(alert_check_daily, CronTrigger(hour=14, minute=50, timezone=TZ), args=["TW"])
    # 美股晨間（美東開盤前，台灣時間晚上）：19:40 新聞 → 20:10 批次 → 20:25 簡報 → 20:40 委託（21:30 開盤成交）
    scheduler.add_job(news_research_daily, CronTrigger(hour=19, minute=40, timezone=TZ), args=["US"])
    scheduler.add_job(ai_batch_daily, CronTrigger(hour=20, minute=10, timezone=TZ), args=["US"])
    scheduler.add_job(overview_daily, CronTrigger(hour=20, minute=25, timezone=TZ), args=["US"])
    scheduler.add_job(sim_decide_daily, CronTrigger(hour=20, minute=40, timezone=TZ), args=["US"])
    # 美股收盤後（台灣清晨）：05:30 同步 → 05:35 撮合 → 05:45 淨值 → 05:50 警示
    scheduler.add_job(sync_market_daily, CronTrigger(hour=5, minute=30, timezone=TZ), args=["US"])
    scheduler.add_job(sim_fill_daily, CronTrigger(hour=5, minute=35, timezone=TZ), args=["US"])
    scheduler.add_job(nav_snapshot_daily, CronTrigger(hour=5, minute=45, timezone=TZ), args=["US"])
    scheduler.add_job(alert_check_daily, CronTrigger(hour=5, minute=50, timezone=TZ), args=["US"])
    scheduler.add_job(maintenance_daily, CronTrigger(hour=3, minute=15, timezone=TZ))
    # 盤中出場哨兵（每小時；非交易日/時段由哨兵自行 no-op）
    scheduler.add_job(exit_sentinel_job, CronTrigger(hour="9-13", minute=10, timezone=TZ), args=["TW"])
    scheduler.add_job(exit_sentinel_job, CronTrigger(hour="21-23,0-4", minute=40, timezone=TZ), args=["US"])
    scheduler.start()
    logger.info("APScheduler started (internal mode)")
    return scheduler
