"""模擬交易撮合引擎。

規則（docs/SD.md §3）：
- 委託單於 AI 決策時建立為 pending，於「下一個有價格的交易日」以開盤價成交
- 台股費用：手續費 0.1425%（最低 20 元），賣出另課證交稅 0.3%
- 美股費用：0（主流券商零手續費）
- 事件溯源：orders 一經 filled/rejected 不再變更；持倉由重放推導
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyPrice, SimAccount, SimOrder, Stock

logger = logging.getLogger(__name__)

INITIAL_CASH = {"TW": 1_000_000.0, "US": 30_000.0}
CURRENCY = {"TW": "TWD", "US": "USD"}

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
    pending = db.execute(
        select(SimOrder, Stock)
        .join(Stock, SimOrder.stock_id == Stock.id)
        .join(SimAccount, SimOrder.account_id == SimAccount.id)
        .where(SimAccount.market == market, SimOrder.status == "pending")
        .order_by(SimOrder.created_at)
    ).all()

    filled = rejected = waiting = 0
    for order, stock in pending:
        price_row = db.execute(
            select(DailyPrice)
            .where(
                DailyPrice.stock_id == stock.id,
                DailyPrice.date > order.created_at.date(),
                DailyPrice.open.is_not(None),
            )
            .order_by(DailyPrice.date)
            .limit(1)
        ).scalar_one_or_none()
        if price_row is None:
            waiting += 1  # 下一個交易日資料尚未同步
            continue

        open_price = float(price_row.open)
        qty = float(order.qty)

        if order.side == "buy":
            # 現金不足則縮量（台股整數股、美股兩位小數）
            while qty > 0:
                gross = qty * open_price
                fee = calc_fee(market, "buy", gross)
                if gross + fee <= float(account.cash):
                    break
                qty = float(int(qty - 1)) if market == "TW" else round(qty - 0.01, 2)
            if qty <= 0:
                _reject(order, "開盤價高於預期，現金不足")
                rejected += 1
                continue
            gross = qty * open_price
            fee = calc_fee(market, "buy", gross)
            account.cash = float(account.cash) - gross - fee
        else:
            gross = qty * open_price
            fee = calc_fee(market, "sell", gross)
            account.cash = float(account.cash) + gross - fee

        order.qty = qty
        order.fill_price = open_price
        order.fee = fee
        order.status = "filled"
        order.filled_at = datetime.combine(price_row.date, datetime.min.time())
        filled += 1
        logger.info(
            "filled %s %s %s x%.2f @ %.2f fee=%.2f",
            market, order.side, stock.symbol, qty, open_price, fee,
        )

    db.commit()
    return {"market": market, "filled": filled, "rejected": rejected, "waiting": waiting}


def _reject(order: SimOrder, reason: str) -> None:
    order.status = "rejected"
    order.reject_reason = reason
