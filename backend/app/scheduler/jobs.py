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
from app.core.exceptions import UpstreamError
from app.models import DailyPrice, Stock, WatchlistItem
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
    """同步該市場所有自選股。單檔失敗不中斷其他檔。

    完成後檢查資料是否真的推進到「最近一個已收盤 session」——上游資料
    尚未就緒時每檔都會靜靜回傳 0 筆，光看 synced 數字看不出來，
    曾因此讓行情停在前一日卻回報成功。
    """
    if gate := _non_trading_gate(market):
        return gate
    from sqlalchemy import func

    from app.services.sim.decision import _latest_session

    db = SessionLocal()
    synced, changed, failed = 0, 0, []
    try:
        stocks = db.execute(
            select(Stock)
            .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
            .where(Stock.market == market)
        ).scalars().all()
        for stock in stocks:
            try:
                changed += await sync_prices(stock.id, stock.market, stock.symbol)
                synced += 1
            except Exception:
                logger.exception("sync %s/%s failed", market, stock.symbol)
                failed.append(stock.symbol)

        latest = db.execute(
            select(func.max(DailyPrice.date))
            .join(Stock, Stock.id == DailyPrice.stock_id)
            .where(Stock.market == market)
        ).scalar_one_or_none()
        expected = _latest_session(market)
        result = {
            "market": market, "synced": synced, "rows_changed": changed,
            "failed": failed,
            "latest_price_date": latest.isoformat() if latest else None,
            "expected_session": expected.isoformat(),
        }
        if latest is None or latest < expected:
            raise UpstreamError(
                f"{market} 同步後行情仍停在 {latest}（預期至少 {expected}）——"
                "上游可能尚未提供該交易日資料，請確認排程時間是否早於資料源更新"
            )
        return result
    finally:
        db.close()  # job 結束即釋放連線


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


async def backup_db_daily() -> dict:
    """每日 pg_dump 備份（單機自架 Postgres 的必要配套；SQLite 開發環境自動跳過）。"""
    import asyncio

    from app.services.backup_service import run_db_backup

    return await asyncio.to_thread(run_db_backup)


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
    "sim-decide-tw": lambda: sim_decide_daily("TW"),
    "sim-decide-us": lambda: sim_decide_daily("US"),
    "sim-fill-tw": lambda: sim_fill_daily("TW"),
    "sim-fill-us": lambda: sim_fill_daily("US"),
    "alerts-tw": lambda: alert_check_daily("TW"),
    "alerts-us": lambda: alert_check_daily("US"),
    "sentinel-tw": lambda: exit_sentinel_job("TW"),
    "sentinel-us": lambda: exit_sentinel_job("US"),
    "maintenance": maintenance_daily,
    "backup-db": backup_db_daily,
}


def _enqueue_scheduled(name: str) -> None:
    """內部排程一律走 JobRun 佇列：執行紀錄進「工作中心」。
    idempotency key（scheduled:{name}）防同名工作排隊中重複入列
    （也涵蓋任何手動/外部觸發撞上內部排程的情況）。"""
    from app.services.job_service import enqueue_job

    enqueue_job(name, job_type="scheduled", payload={"name": name},
                idempotency_key=f"scheduled:{name}")


def start_sentinel_scheduler() -> AsyncIOScheduler:
    """external 模式專用：只排「盤中出場哨兵」。

    每日大序列仍由 GitHub Actions 觸發（延遲無害），但哨兵需要分鐘級準時，
    GH cron 的 1~2 小時延遲會讓每小時巡邏名存實亡——改由後端自己的時鐘執行。
    前提：盤中需以外部 uptime ping（如 UptimeRobot 打 /health/live）保持
    Render 清醒。哨兵僅由此觸發（GH 的哨兵 cron 已移除——延遲 1~2 小時
    的備援幾乎無實益，徒增噪音）。
    """
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(_enqueue_scheduled, CronTrigger(hour="9-13", minute=10, timezone=TZ), args=["sentinel-tw"])
    scheduler.add_job(_enqueue_scheduled, CronTrigger(hour="21-23,0-3", minute=40, timezone=TZ), args=["sentinel-us"])
    scheduler.add_job(_enqueue_scheduled, CronTrigger(hour=3, minute=55, timezone=TZ), args=["sentinel-us"])
    scheduler.start()
    logger.info("Sentinel-only APScheduler started (external mode)")
    return scheduler


def start_scheduler() -> AsyncIOScheduler:
    """internal 模式：全部排程走後端時鐘，並一律經 JobRun 佇列執行——
    每一輪都在「工作中心」留下觸發時間與結果，準時與否有據可查。"""
    scheduler = AsyncIOScheduler(timezone=TZ)

    def at(hour, minute, name):
        scheduler.add_job(
            _enqueue_scheduled, CronTrigger(hour=hour, minute=minute, timezone=TZ), args=[name]
        )

    # 分析/決策＝開盤前晨間（已消化昨收＋隔夜美股/國際盤）；成交於當日開盤價。
    # 資料任務（同步/撮合/淨值/警示）＝收盤後。
    #
    # 台股晨間：06:10 新聞 → 06:40 AI 批次 → 06:55 簡報 → 07:10 產生委託（09:00 開盤成交）
    at(6, 10, "news-tw")
    at(6, 40, "ai-batch-tw")
    at(6, 55, "overview-tw")
    at(7, 10, "sim-decide-tw")
    # 台股收盤後：14:45 淨值 → 16:30 同步 → 16:40 撮合晨間委託 → 16:50 警示
    # 淨值早、行情晚是刻意的：證交所 all_etf.txt 收盤即有當日淨值（實測
    # 13:52 已可取得），但 FinMind 的台股日線同一時間仍只到前一交易日，
    # 原本 14:30（收盤後 1 小時）同步會抓不到當日資料。
    at(14, 45, "nav-tw")
    at(16, 30, "sync-tw")
    at(16, 40, "sim-fill-tw")
    at(16, 50, "alerts-tw")
    # 美股晨間（美東開盤前，台灣時間晚上）：19:40 新聞 → 20:10 批次 → 20:25 簡報 → 20:40 委託（21:30 開盤成交）
    at(19, 40, "news-us")
    at(20, 10, "ai-batch-us")
    at(20, 25, "overview-us")
    at(20, 40, "sim-decide-us")
    # 美股收盤後（台灣上午）：08:00 同步 → 08:10 撮合 → 08:25 警示
    # （無淨值快照：免費資料源不提供美股 ETF 淨值，折溢價僅台股支援）
    # 刻意不排在收盤後 1~2 小時：FinMind 的美股日線要數小時才會就緒，
    # 原本 05:30（收盤後 1.5h）抓不到當日資料卻回報成功，行情因此停在前一日。
    # 美股收盤 16:00 ET＝台灣 04:00（夏令）/05:00（冬令），08:00 兩季皆有 3 小時以上緩衝。
    at(8, 0, "sync-us")
    at(8, 10, "sim-fill-us")
    at(8, 25, "alerts-us")
    at(3, 15, "maintenance")
    at(17, 30, "backup-db")  # 每日 DB 備份（台股收盤序列之後的閒置時段）
    # 盤中出場哨兵（每小時；非交易日/時段由哨兵自行 no-op）
    at("9-13", 10, "sentinel-tw")
    at("21-23,0-4", 40, "sentinel-us")
    scheduler.start()
    logger.info("APScheduler started (internal mode)")
    return scheduler
