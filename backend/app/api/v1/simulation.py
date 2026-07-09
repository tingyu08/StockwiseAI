import json
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.models import AiReport, SimAccount, SimOrder, Stock
from app.services.sim.decision import run_decisions
from app.services.sim.engine import fill_pending_orders, get_or_create_account
from app.services.sim.portfolio import equity_curve, positions_dto

router = APIRouter(tags=["simulation"])

Market = Literal["TW", "US"]


@router.get("/simulation/{market}/account", response_model=Envelope)
async def account_view(market: Market, db: Session = Depends(get_db)) -> Envelope:
    account = get_or_create_account(db, market)
    positions = positions_dto(db, account)
    curve = equity_curve(db, account)
    holdings_value = sum(p["market_value"] or 0 for p in positions)
    equity = round(float(account.cash) + holdings_value, 2)
    return ok(
        {
            "market": market,
            "currency": account.currency,
            "initial_cash": float(account.initial_cash),
            "cash": round(float(account.cash), 2),
            "equity": equity,
            "total_pnl": round(equity - float(account.initial_cash), 2),
            "total_pnl_pct": round(
                (equity - float(account.initial_cash)) / float(account.initial_cash) * 100, 2
            ),
            "positions": positions,
            "equity_curve": curve,
        }
    )


@router.get("/simulation/{market}/orders", response_model=Envelope)
async def orders_view(market: Market, db: Session = Depends(get_db)) -> Envelope:
    account = get_or_create_account(db, market)
    rows = db.execute(
        select(SimOrder, Stock)
        .join(Stock, SimOrder.stock_id == Stock.id)
        .where(SimOrder.account_id == account.id)
        .order_by(SimOrder.created_at.desc())
        .limit(200)
    ).all()
    out = []
    for order, stock in rows:
        report = db.get(AiReport, order.ai_report_id) if order.ai_report_id else None
        out.append(
            {
                "id": order.id,
                "symbol": stock.symbol,
                "name": stock.name,
                "side": order.side,
                "qty": float(order.qty),
                "fill_price": float(order.fill_price) if order.fill_price else None,
                "fee": float(order.fee) if order.fee else None,
                "status": order.status,
                "decided_by": order.decided_by,
                "reject_reason": order.reject_reason,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None,
                "ai_report": json.loads(report.payload_json) if report else None,
            }
        )
    return ok(out)


@router.post("/simulation/{market}:decide", response_model=Envelope)
async def trigger_decisions(market: Market, db: Session = Depends(get_db)) -> Envelope:
    """手動觸發 AI 決策。會先自動對託管股跑當日批次分析（有快取不重複扣額度）。"""
    from sqlalchemy import select

    from app.models import WatchlistItem
    from app.services.analysis_service import run_batch

    managed = db.execute(
        select(Stock)
        .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
        .where(Stock.market == market, WatchlistItem.ai_managed.is_(True))
    ).scalars().all()
    batch_result = await run_batch(db, managed) if managed else {"analyzed": 0, "skipped": 0}
    result = run_decisions(db, market)
    return ok({**result, "batch": batch_result})


@router.post("/simulation/{market}:fill", response_model=Envelope)
async def trigger_fill(market: Market, db: Session = Depends(get_db)) -> Envelope:
    """手動觸發撮合（正式流程於每日資料同步後執行）。"""
    return ok(fill_pending_orders(db, market))
