"""持倉與權益曲線 — 由 filled orders 事件溯源重放，不另存狀態。"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyPrice, SimAccount, SimOrder, Stock


def current_positions(db: Session, account: SimAccount) -> dict[int, float]:
    """{stock_id: qty}，重放全部 filled orders。"""
    orders = db.execute(
        select(SimOrder)
        .where(SimOrder.account_id == account.id, SimOrder.status == "filled")
        .order_by(SimOrder.filled_at)
    ).scalars().all()
    positions: dict[int, float] = {}
    for o in orders:
        delta = float(o.qty) if o.side == "buy" else -float(o.qty)
        positions[o.stock_id] = round(positions.get(o.stock_id, 0.0) + delta, 4)
    return {sid: q for sid, q in positions.items() if q > 0}


def positions_dto(db: Session, account: SimAccount) -> list[dict]:
    positions = current_positions(db, account)
    out = []
    for stock_id, qty in positions.items():
        stock = db.get(Stock, stock_id)
        last = db.execute(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock_id, DailyPrice.close.is_not(None))
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        # 平均成本：重放買賣（賣出按比例沖銷成本）
        avg_cost = _avg_cost(db, account.id, stock_id)
        close = float(last.close) if last else None
        market_value = round(qty * close, 2) if close else None
        out.append(
            {
                "symbol": stock.symbol,
                "name": stock.name,
                "qty": qty,
                "avg_cost": avg_cost,
                "close": close,
                "market_value": market_value,
                "unrealized_pnl": round((close - avg_cost) * qty, 2)
                if close and avg_cost else None,
                "unrealized_pnl_pct": round((close - avg_cost) / avg_cost * 100, 2)
                if close and avg_cost else None,
            }
        )
    return out


def _avg_cost(db: Session, account_id: int, stock_id: int) -> float | None:
    orders = db.execute(
        select(SimOrder)
        .where(
            SimOrder.account_id == account_id,
            SimOrder.stock_id == stock_id,
            SimOrder.status == "filled",
        )
        .order_by(SimOrder.filled_at)
    ).scalars().all()
    qty = 0.0
    cost = 0.0
    for o in orders:
        if o.side == "buy":
            cost += float(o.qty) * float(o.fill_price) + float(o.fee or 0)
            qty += float(o.qty)
        else:
            if qty > 0:
                cost -= cost * (float(o.qty) / qty)  # 按比例沖銷
            qty -= float(o.qty)
    return round(cost / qty, 2) if qty > 0 else None


def equity_curve(db: Session, account: SimAccount) -> list[dict]:
    """每日權益 = 現金 + Σ(持股 × 當日收盤)。從第一筆成交起算。"""
    orders = db.execute(
        select(SimOrder)
        .where(SimOrder.account_id == account.id, SimOrder.status == "filled")
        .order_by(SimOrder.filled_at)
    ).scalars().all()
    if not orders:
        return []

    start = orders[0].filled_at.date()
    stock_ids = {o.stock_id for o in orders}
    prices = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id.in_(stock_ids), DailyPrice.date >= start)
        .order_by(DailyPrice.date)
    ).scalars().all()
    close_by_day: dict[date, dict[int, float]] = {}
    for p in prices:
        if p.close is not None:
            close_by_day.setdefault(p.date, {})[p.stock_id] = float(p.close)

    curve = []
    cash = float(account.initial_cash)
    positions: dict[int, float] = {}
    last_close: dict[int, float] = {}
    order_idx = 0
    for day in sorted(close_by_day):
        while order_idx < len(orders) and orders[order_idx].filled_at.date() <= day:
            o = orders[order_idx]
            gross = float(o.qty) * float(o.fill_price)
            fee = float(o.fee or 0)
            if o.side == "buy":
                cash -= gross + fee
                positions[o.stock_id] = positions.get(o.stock_id, 0.0) + float(o.qty)
            else:
                cash += gross - fee
                positions[o.stock_id] = positions.get(o.stock_id, 0.0) - float(o.qty)
            order_idx += 1
        last_close.update(close_by_day[day])
        holdings = sum(
            qty * last_close.get(sid, 0.0) for sid, qty in positions.items() if qty > 0
        )
        curve.append({"date": day.isoformat(), "equity": round(cash + holdings, 2)})
    return curve
