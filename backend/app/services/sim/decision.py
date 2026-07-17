"""AI 決策 → 委託單。風控為硬規則寫死在程式，不交給 AI 判斷。

規則：
- 買進：當日報告 action=buy 且 confidence >= 0.7、無持倉、通過部位/現金限制
- 賣出：action=sell 且 confidence >= 0.6、有持倉 → 全數出清
- 停損：收盤跌破「建倉時報告的 stop_loss」→ 強制全數出清（不看信心）
- 單一持股上限：權益的 20%；現金保留下限：權益的 10%
"""
import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AiReport, DailyPrice, SimOrder, Stock, WatchlistItem
from app.services.sim.engine import calc_fee, get_or_create_account
from app.services.sim.portfolio import current_positions
from app.services.time_service import utc_now_naive
from app.services.trading_calendar import last_trading_session

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
    skipped: list[dict] = []
    buy_candidates: list[tuple[float, Stock, float, AiReport]] = []
    expected_session = _latest_session(market)
    for stock in managed:
        if _has_pending(db, account.id, stock.id):
            skipped.append({"symbol": stock.symbol, "reason": "已有待成交委託"})
            continue
        last_close = _last_close(db, stock.id)
        if last_close is None:
            skipped.append({"symbol": stock.symbol, "reason": "無價格資料"})
            continue
        # 價格新鮮度閘門：資料未更新至最新交易日 → 寧可不動作，不用舊價下單
        if _last_price_date(db, stock.id) < expected_session:
            skipped.append(
                {"symbol": stock.symbol, "reason": "價格尚未更新至最新交易日，跳過決策"}
            )
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
            skipped.append({"symbol": stock.symbol, "reason": "無最新交易日的 AI 報告（請先產生分析）"})
            continue
        confidence = float(report.confidence or 0)

        # 2) 賣出訊號
        if report.action == "sell":
            if held_qty <= 0:
                skipped.append({"symbol": stock.symbol, "reason": "AI 建議賣出，但無持倉"})
            elif confidence < SELL_CONFIDENCE:
                skipped.append({"symbol": stock.symbol, "reason": f"賣出信心 {confidence:.0%} 未達門檻 {SELL_CONFIDENCE:.0%}"})
            else:
                db.add(_make_order(account.id, stock.id, "sell", held_qty, report.id))
                created.append({"symbol": stock.symbol, "side": "sell", "reason": "ai-signal"})
            continue

        # 3) 買進訊號（已有持倉不加碼，控制單一持股曝險）
        if report.action == "buy":
            if held_qty > 0:
                skipped.append({"symbol": stock.symbol, "reason": "已有持倉，不加碼"})
            elif confidence < BUY_CONFIDENCE:
                skipped.append({"symbol": stock.symbol, "reason": f"買進信心 {confidence:.0%} 未達門檻 {BUY_CONFIDENCE:.0%}"})
            else:
                buy_candidates.append((confidence, stock, last_close, report))
            continue

        skipped.append({"symbol": stock.symbol, "reason": "AI 建議觀望（hold）"})

    available_cash = float(account.cash)
    for _confidence, stock, last_close, report in sorted(
        buy_candidates, key=lambda item: (-item[0], item[1].symbol)
    ):
        qty = _size_buy(
            db, account, equity, last_close, market, cash_available=available_cash
        )
        if qty <= 0:
            skipped.append(
                {"symbol": stock.symbol, "reason": "部位上限/現金保留限制，無可用額度"}
            )
            continue
        gross = qty * last_close
        available_cash -= gross + calc_fee(market, "buy", gross)
        db.add(_make_order(account.id, stock.id, "buy", qty, report.id))
        created.append({"symbol": stock.symbol, "side": "buy", "qty": qty})

    db.commit()
    return {
        "market": market,
        "managed": len(managed),
        "orders_created": len(created),
        "orders": created,
        "skipped": skipped,
    }


def _size_buy(
    db: Session,
    account,
    equity: float,
    price: float,
    market: str,
    cash_available: float | None = None,
) -> float:
    max_value = equity * MAX_POSITION_PCT
    available = (
        float(account.cash) if cash_available is None else cash_available
    ) - equity * MIN_CASH_PCT
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
    """最新交易日的報告（trade → deep → routine）。"""
    latest_date = db.execute(
        select(AiReport.trade_date)
        .where(
            AiReport.stock_id == stock_id,
            AiReport.kind.in_(("trade", "deep", "routine")),
        )
        .order_by(AiReport.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_date is None or latest_date < _last_price_date(db, stock_id):
        return None  # 報告過期（未涵蓋最新交易日）不據以決策
    for kind in ("trade", "deep", "routine"):
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


MARKET_CLOSE = {"TW": (13, 30), "US": (16, 0)}  # 當地收盤時間


def _latest_session(market: str, now: datetime | None = None) -> date:
    """最近一個「已收盤」的交易日——決策資料必須新鮮到這一天。

    晨間（開盤前）決策：今天還沒收盤 → 要求資料到昨天的 session；
    收盤後決策：要求資料到今天。
    """
    from datetime import timezone

    from app.services.time_service import MARKET_TIMEZONES

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(MARKET_TIMEZONES[market])
    today = local.date()
    session = last_trading_session(market, today)
    if session == today and (local.hour, local.minute) < MARKET_CLOSE[market]:
        return last_trading_session(market, today - timedelta(days=1))
    return session


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
        created_at=utc_now_naive(),
    )
