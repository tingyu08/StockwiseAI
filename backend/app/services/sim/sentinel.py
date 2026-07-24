"""盤中出場哨兵：對「持倉」做停損/停利的即時檢查（不做買入，維持進場日線紀律）。

- 純硬規則、零 AI 呼叫：停損價與目標價來自建倉當時的 AI 報告
- 觸發即以「當下觀察到的報價」成交（等同停損市價單的近似），
  訂單標記 fill_kind = stop_loss / take_profit，與日線「隔日開盤成交」區分
- 併發安全：沿用 pending 單的 partial unique index——先建 pending 再立即成交，
  同股已有 pending（含每日決策排隊中的單）時自動跳過
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AiReport, SimOrder, Stock
from app.providers.market.intraday import fetch_intraday_quotes
from app.services.sim.engine import calc_fee, get_or_create_account
from app.services.sim.portfolio import current_positions
from app.services.time_service import MARKET_TIMEZONES, market_today, utc_now_naive
from app.services.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

# 盤中時段（當地時間，含少量收盤緩衝）
MARKET_HOURS = {"TW": ((9, 0), (13, 35)), "US": ((9, 30), (16, 5))}


async def run_exit_sentinel(db: Session, market: str) -> dict:
    today = market_today(market)
    if not is_trading_day(market, today):
        return {"market": market, "skipped": "非交易日", "checked": 0, "exits": []}
    if not _in_market_hours(market):
        return {"market": market, "skipped": "非交易時段", "checked": 0, "exits": []}

    account = get_or_create_account(db, market)
    positions = current_positions(db, account)
    if not positions:
        return {"market": market, "checked": 0, "exits": []}

    stocks = {
        s.id: s
        for s in db.execute(select(Stock).where(Stock.id.in_(positions))).scalars()
    }
    quotes = await fetch_intraday_quotes(
        market, [stocks[sid].symbol for sid in positions if sid in stocks]
    )

    exits: list[dict] = []
    unpriced: list[str] = []
    for stock_id, qty in positions.items():
        stock = stocks.get(stock_id)
        if stock is None:
            continue
        quote = quotes.get(stock.symbol)
        if quote is None:
            logger.info("sentinel %s：無報價，本輪跳過", stock.symbol)
            unpriced.append(stock.symbol)
            continue
        stop, target, report_id = _entry_exit_levels(db, account.id, stock_id)

        fill_kind: str | None = None
        if stop is not None and quote <= stop:
            fill_kind = "stop_loss"
        elif target is not None and quote >= target:
            fill_kind = "take_profit"
        if fill_kind is None:
            continue

        if not _fill_exit(
            db, account, stock_id, qty, quote, report_id, fill_kind,
            is_etf=stock.kind == "etf",
        ):
            continue  # 已有 pending（每日決策單或並發哨兵），讓既有流程處理
        exits.append(
            {
                "symbol": stock.symbol,
                "kind": fill_kind,
                "qty": qty,
                "price": quote,
                "trigger": stop if fill_kind == "stop_loss" else target,
            }
        )
        logger.info(
            "sentinel exit %s %s x%.2f @ %.2f (%s)",
            market, stock.symbol, qty, quote, fill_kind,
        )

    return {
        "market": market,
        "checked": len(positions),
        "exits": exits,
        # 有持倉但當輪拿不到可成交價的標的（跌停鎖死/暫停交易等），供工作中心檢視
        "unpriced": unpriced,
    }


def _fill_exit(
    db: Session,
    account,
    stock_id: int,
    qty: float,
    price: float,
    report_id: int | None,
    fill_kind: str,
    is_etf: bool = False,
) -> bool:
    """建 pending（吃 partial unique index 防重複）後立即以觀察價成交。"""
    order = SimOrder(
        account_id=account.id,
        stock_id=stock_id,
        side="sell",
        qty=qty,
        status="pending",
        decided_by="ai",
        ai_report_id=report_id,
        created_at=utc_now_naive(),
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False

    gross = qty * price
    fee = calc_fee(account.market, "sell", gross, is_etf=is_etf)
    account.cash = float(account.cash) + gross - fee
    order.fill_price = price
    order.fee = fee
    order.status = "filled"
    order.fill_kind = fill_kind
    order.filled_at = utc_now_naive()
    db.commit()
    return True


def _entry_exit_levels(
    db: Session, account_id: int, stock_id: int
) -> tuple[float | None, float | None, int | None]:
    """最近一次建倉買單所附報告的 (stop_loss, target_price_high, report_id)。"""
    buy = db.execute(
        select(SimOrder)
        .where(
            SimOrder.account_id == account_id,
            SimOrder.stock_id == stock_id,
            SimOrder.side == "buy",
            SimOrder.status == "filled",
            SimOrder.ai_report_id.is_not(None),
        )
        .order_by(SimOrder.filled_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if buy is None:
        return None, None, None
    report = db.get(AiReport, buy.ai_report_id)
    if report is None:
        return None, None, None
    try:
        payload = json.loads(report.payload_json)
    except ValueError:
        return None, None, buy.ai_report_id
    return (
        _to_float(payload.get("stop_loss")),
        _to_float(payload.get("target_price_high")),
        buy.ai_report_id,
    )


def _to_float(value) -> float | None:
    try:
        result = float(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _in_market_hours(market: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(MARKET_TIMEZONES[market])
    (open_h, open_m), (close_h, close_m) = MARKET_HOURS[market]
    minutes = local.hour * 60 + local.minute
    return open_h * 60 + open_m <= minutes <= close_h * 60 + close_m
