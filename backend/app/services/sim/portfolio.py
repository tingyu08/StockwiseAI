"""持倉與權益曲線 — 由 filled orders 事件溯源重放，不另存狀態。"""
from datetime import date

from sqlalchemy import and_, func, select
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
    orders = db.execute(
        select(SimOrder)
        .where(SimOrder.account_id == account.id, SimOrder.status == "filled")
        .order_by(SimOrder.filled_at, SimOrder.id)
    ).scalars().all()
    positions, average_costs = _position_state(orders)
    if not positions:
        return []

    stock_ids = list(positions)
    stocks = {
        stock.id: stock
        for stock in db.execute(select(Stock).where(Stock.id.in_(stock_ids))).scalars()
    }
    latest_dates = (
        select(DailyPrice.stock_id, func.max(DailyPrice.date).label("latest_date"))
        .where(DailyPrice.stock_id.in_(stock_ids), DailyPrice.close.is_not(None))
        .group_by(DailyPrice.stock_id)
        .subquery()
    )
    latest_prices = {
        price.stock_id: price
        for price in db.execute(
            select(DailyPrice).join(
                latest_dates,
                and_(
                    DailyPrice.stock_id == latest_dates.c.stock_id,
                    DailyPrice.date == latest_dates.c.latest_date,
                ),
            )
        ).scalars()
    }

    out = []
    for stock_id, qty in positions.items():
        stock = stocks[stock_id]
        last = latest_prices.get(stock_id)
        avg_cost = average_costs.get(stock_id)
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


def _position_state(
    orders: list[SimOrder],
) -> tuple[dict[int, float], dict[int, float]]:
    quantities: dict[int, float] = {}
    costs: dict[int, float] = {}
    for o in orders:
        stock_id = o.stock_id
        qty = quantities.get(stock_id, 0.0)
        cost = costs.get(stock_id, 0.0)
        if o.side == "buy":
            cost += float(o.qty) * float(o.fill_price) + float(o.fee or 0)
            qty += float(o.qty)
        else:
            if qty > 0:
                cost -= cost * (float(o.qty) / qty)  # 按比例沖銷
            qty -= float(o.qty)
        quantities[stock_id] = round(qty, 4)
        costs[stock_id] = cost
    active = {stock_id: qty for stock_id, qty in quantities.items() if qty > 0}
    averages = {
        stock_id: round(costs[stock_id] / qty, 2)
        for stock_id, qty in active.items()
    }
    return active, averages


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
