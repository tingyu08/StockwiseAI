"""模擬交易撮合引擎。

規則（docs/SD.md §3）：
- 委託單於 AI 決策時建立為 pending，以「決策後第一個開盤」的開盤價成交：
  開盤前（晨間決策流程）建立的單吃當地「當天」開盤；開盤後建立的單吃下一個交易日
- 台股費用：手續費 0.1425%（最低 20 元），賣出另課證交稅 0.3%
- 美股費用：0（主流券商零手續費）
- 事件溯源：orders 一經 filled/rejected 不再變更；持倉由重放推導
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import DailyPrice, SimAccount, SimOrder, Stock
from app.services.sim.portfolio import current_positions
from app.services.time_service import MARKET_TIMEZONES

logger = logging.getLogger(__name__)

INITIAL_CASH = {"TW": 1_000_000.0, "US": 30_000.0}
CURRENCY = {"TW": "TWD", "US": "USD"}
MARKET_OPEN = {"TW": (9, 0), "US": (9, 30)}  # 當地開盤時間

TW_FEE_RATE = 0.001425
TW_FEE_MIN = 20.0
TW_TAX_RATE = 0.003  # 賣出證交稅


def get_or_create_account(db: Session, market: str) -> SimAccount:
    account = db.execute(
        select(SimAccount).where(SimAccount.market == market)
    ).scalar_one_or_none()
    if account is None:
        account = SimAccount(
            market=market,
            currency=CURRENCY[market],
            initial_cash=INITIAL_CASH[market],
            cash=INITIAL_CASH[market],
        )
        db.add(account)
        db.commit()
        db.refresh(account)
    return account


def calc_fee(market: str, side: str, gross: float) -> float:
    """交易成本（手續費＋稅）。"""
    if market == "US":
        return 0.0
    fee = max(TW_FEE_MIN, gross * TW_FEE_RATE)
    if side == "sell":
        fee += gross * TW_TAX_RATE
    return round(fee, 2)


def fill_pending_orders(db: Session, market: str) -> dict:
    """撮合所有 pending 單：以委託建立後第一個交易日的開盤價成交。

    現金不足（開盤價高於決策時估價）→ 縮量成交；縮到 0 → rejected。
    """
    account = get_or_create_account(db, market)
    positions = current_positions(db, account)
    pending = db.execute(
        select(SimOrder, Stock)
        .join(Stock, SimOrder.stock_id == Stock.id)
        .join(SimAccount, SimOrder.account_id == SimAccount.id)
        .where(SimAccount.market == market, SimOrder.status == "pending")
        .order_by(SimOrder.created_at)
    ).all()

    filled = rejected = waiting = 0
    for order, stock in pending:
        if not _claim_pending_order(db, order.id):
            continue
        order.status = "filling"
        price_row = db.execute(
            select(DailyPrice)
            .where(
                DailyPrice.stock_id == stock.id,
                DailyPrice.date >= _earliest_fill_date(order.created_at, market),
                DailyPrice.open.is_not(None),
            )
            .order_by(DailyPrice.date)
            .limit(1)
        ).scalar_one_or_none()
        if price_row is None:
            # claim 是原生 UPDATE（session 內屬性仍是 'pending'），
            # 還原必須也走 UPDATE——只改屬性會被 SQLAlchemy 視為無變更而不寫回，
            # 訂單將永久卡在 'filling' 無法再被撮合
            db.execute(
                update(SimOrder)
                .where(SimOrder.id == order.id)
                .values(status="pending")
                .execution_options(synchronize_session=False)
            )
            order.status = "pending"
            waiting += 1  # 下一個交易日資料尚未同步
            continue

        open_price = float(price_row.open)
        qty = float(order.qty)

        if order.side == "buy":
            qty = _affordable_qty(
                float(account.cash), open_price, market, max_qty=qty
            )
            if qty <= 0:
                _reject(order, "開盤價高於預期，現金不足")
                rejected += 1
                continue
            gross = qty * open_price
            fee = calc_fee(market, "buy", gross)
            account.cash = float(account.cash) - gross - fee
        else:
            held_qty = positions.get(stock.id, 0.0)
            if qty > held_qty + 1e-9:
                _reject(order, "賣出數量超過目前持倉")
                rejected += 1
                continue
            gross = qty * open_price
            fee = calc_fee(market, "sell", gross)
            account.cash = float(account.cash) + gross - fee

        order.qty = qty
        order.fill_price = open_price
        order.fee = fee
        order.status = "filled"
        order.filled_at = datetime.combine(price_row.date, datetime.min.time())
        delta = qty if order.side == "buy" else -qty
        positions[stock.id] = round(positions.get(stock.id, 0.0) + delta, 4)
        filled += 1
        logger.info(
            "filled %s %s %s x%.2f @ %.2f fee=%.2f",
            market, order.side, stock.symbol, qty, open_price, fee,
        )

    db.commit()
    return {"market": market, "filled": filled, "rejected": rejected, "waiting": waiting}


def _earliest_fill_date(created_at_utc: datetime, market: str) -> date:
    """委託可成交的最早交易日：開盤前建立 → 當地當天；開盤後建立 → 次一日。

    無前視偏誤：晨間（開盤前）的決策用的是昨收＋隔夜國際盤資料，
    成交於幾小時後的當日開盤價，等同真實世界的開盤市價單。
    """
    aware = (
        created_at_utc.replace(tzinfo=timezone.utc)
        if created_at_utc.tzinfo is None
        else created_at_utc
    )
    local = aware.astimezone(MARKET_TIMEZONES[market])
    if (local.hour, local.minute) < MARKET_OPEN[market]:
        return local.date()
    return local.date() + timedelta(days=1)


def _reject(order: SimOrder, reason: str) -> None:
    order.status = "rejected"
    order.reject_reason = reason


def _claim_pending_order(db: Session, order_id: int) -> bool:
    """Atomically move one pending order into the in-flight state."""
    result = db.execute(
        update(SimOrder)
        .where(SimOrder.id == order_id, SimOrder.status == "pending")
        .values(status="filling")
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _affordable_qty(
    cash: float, price: float, market: str, max_qty: float | None = None
) -> float:
    """Largest affordable whole/fractional quantity via O(log n) search."""
    if cash <= 0 or price <= 0:
        return 0.0
    scale = 1 if market == "TW" else 100
    high = int(cash / price * scale)
    if max_qty is not None:
        high = min(high, int(max_qty * scale + 1e-9))
    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        qty = mid / scale
        gross = qty * price
        if gross + calc_fee(market, "buy", gross) <= cash + 1e-9:
            low = mid
        else:
            high = mid - 1
    return float(low) if market == "TW" else round(low / scale, 2)
