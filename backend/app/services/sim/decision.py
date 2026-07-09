"""AI 決策 → 委託單。風控為硬規則寫死在程式，不交給 AI 判斷。

規則：
- 買進：當日報告 action=buy 且 confidence >= 0.7、無持倉、通過部位/現金限制
- 賣出：action=sell 且 confidence >= 0.6、有持倉 → 全數出清
- 停損：收盤跌破「建倉時報告的 stop_loss」→ 強制全數出清（不看信心）
- 單一持股上限：權益的 20%；現金保留下限：權益的 10%
"""
import json
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AiReport, DailyPrice, SimOrder, Stock, WatchlistItem
from app.services.sim.engine import calc_fee, get_or_create_account
from app.services.sim.portfolio import current_positions

logger = logging.getLogger(__name__)

BUY_CONFIDENCE = 0.7
SELL_CONFIDENCE = 0.6
MAX_POSITION_PCT = 0.20
MIN_CASH_PCT = 0.10


def run_decisions(db: Session, market: str) -> dict:
    """對 AI 託管清單依當日報告產生 pending 委託單。"""
    account = get_or_create_account(db, market)
    positions = current_positions(db, account)
    equity = _estimate_equity(db, account, positions)

    managed = db.execute(
        select(Stock)
        .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
        .where(Stock.market == market, WatchlistItem.ai_managed.is_(True))
    ).scalars().all()

    created: list[dict] = []
    for stock in managed:
        if _has_pending(db, account.id, stock.id):
            continue
        last_close = _last_close(db, stock.id)
        if last_close is None:
            continue

        held_qty = positions.get(stock.id, 0.0)

        # 1) 停損（最高優先，硬規則）
        if held_qty > 0:
            stop = _entry_stop_loss(db, account.id, stock.id)
            if stop is not None and last_close < stop:
                order = _make_order(account.id, stock.id, "sell", held_qty, None)
                order.reject_reason = None
                db.add(order)
                created.append({"symbol": stock.symbol, "side": "sell", "reason": "stop-loss"})
                continue

        report = _today_report(db, stock.id)
        if report is None:
            continue
        confidence = float(report.confidence or 0)

        # 2) 賣出訊號
        if report.action == "sell" and held_qty > 0 and confidence >= SELL_CONFIDENCE:
            db.add(_make_order(account.id, stock.id, "sell", held_qty, report.id))
            created.append({"symbol": stock.symbol, "side": "sell", "reason": "ai-signal"})
            continue

        # 3) 買進訊號（已有持倉不加碼，控制單一持股曝險）
        if report.action == "buy" and held_qty == 0 and confidence >= BUY_CONFIDENCE:
            qty = _size_buy(db, account, equity, last_close, market)
            if qty <= 0:
                logger.info("skip buy %s: 部位/現金限制不足", stock.symbol)
                continue
            db.add(_make_order(account.id, stock.id, "buy", qty, report.id))
            created.append({"symbol": stock.symbol, "side": "buy", "qty": qty})

    db.commit()
    return {"market": market, "orders_created": len(created), "orders": created}


def _size_buy(db: Session, account, equity: float, price: float, market: str) -> float:
    max_value = equity * MAX_POSITION_PCT
    available = float(account.cash) - equity * MIN_CASH_PCT
    budget = min(max_value, available)
    if budget <= 0:
        return 0.0
    fee_buffer = calc_fee(market, "buy", budget)
    qty = (budget - fee_buffer) / price
    return float(int(qty)) if market == "TW" else round(max(0.0, qty), 2)


def _estimate_equity(db: Session, account, positions: dict[int, float]) -> float:
    holdings = 0.0
    for sid, qty in positions.items():
        close = _last_close(db, sid)
        if close:
            holdings += qty * close
    return float(account.cash) + holdings


def _last_close(db: Session, stock_id: int) -> float | None:
    row = db.execute(
        select(DailyPrice.close)
        .where(DailyPrice.stock_id == stock_id, DailyPrice.close.is_not(None))
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(row) if row is not None else None


def _today_report(db: Session, stock_id: int) -> AiReport | None:
    """最新交易日的報告（deep 優先）。"""
    latest_date = db.execute(
        select(AiReport.trade_date)
        .where(AiReport.stock_id == stock_id, AiReport.kind.in_(("routine", "deep")))
        .order_by(AiReport.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_date is None or latest_date < _last_price_date(db, stock_id):
        return None  # 報告過期（未涵蓋最新交易日）不據以決策
    for kind in ("deep", "routine"):
        report = db.execute(
            select(AiReport).where(
                AiReport.stock_id == stock_id,
                AiReport.trade_date == latest_date,
                AiReport.kind == kind,
            )
        ).scalar_one_or_none()
        if report:
            return report
    return None


def _last_price_date(db: Session, stock_id: int) -> date:
    return db.execute(
        select(DailyPrice.date)
        .where(DailyPrice.stock_id == stock_id)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).scalar_one()


def _entry_stop_loss(db: Session, account_id: int, stock_id: int) -> float | None:
    """最近一次建倉買單所附報告的 stop_loss。"""
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
        return None
    report = db.get(AiReport, buy.ai_report_id)
    if report is None:
        return None
    try:
        return float(json.loads(report.payload_json).get("stop_loss"))
    except (ValueError, TypeError):
        return None


def _has_pending(db: Session, account_id: int, stock_id: int) -> bool:
    return (
        db.execute(
            select(SimOrder.id).where(
                SimOrder.account_id == account_id,
                SimOrder.stock_id == stock_id,
                SimOrder.status == "pending",
            )
        ).scalar_one_or_none()
        is not None
    )


def _make_order(account_id: int, stock_id: int, side: str, qty: float, report_id: int | None) -> SimOrder:
    return SimOrder(
        account_id=account_id,
        stock_id=stock_id,
        side=side,
        qty=qty,
        status="pending",
        decided_by="ai",
        ai_report_id=report_id,
    )
