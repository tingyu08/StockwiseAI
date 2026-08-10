import json
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.models import AiReport, SimOrder, Stock
from app.services.sim.engine import fill_pending_orders, get_or_create_account
from app.services.sim.portfolio import (
    equity_curve,
    positions_dto,
    realized_pnl_by_order,
)
from app.services.job_service import enqueue_job
from app.services.time_service import as_utc_iso

router = APIRouter(tags=["simulation"])

Market = Literal["TW", "US"]


@router.get("/simulation/{market}/account", response_model=Envelope)
def account_view(market: Market, db: Session = Depends(get_db)) -> Envelope:
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
def orders_view(market: Market, db: Session = Depends(get_db)) -> Envelope:
    account = get_or_create_account(db, market)
    rows = db.execute(
        select(SimOrder, Stock)
        .join(Stock, SimOrder.stock_id == Stock.id)
        .where(SimOrder.account_id == account.id)
        .order_by(SimOrder.created_at.desc())
        .limit(200)
    ).all()
    # 一次撈齊本頁的 AI 報告：逐筆 db.get 是 N+1（最壞 200 次額外查詢）
    report_ids = {o.ai_report_id for o, _ in rows if o.ai_report_id}
    reports = (
        {
            r.id: r
            for r in db.execute(
                select(AiReport).where(AiReport.id.in_(report_ids))
            ).scalars()
        }
        if report_ids
        else {}
    )
    # 賣出的已實現損益由全部成交紀錄重放推導（與持倉均價同一套口徑）
    realized = realized_pnl_by_order(db, account)
    out = []
    for order, stock in rows:
        report = reports.get(order.ai_report_id)
        fill_price = float(order.fill_price) if order.fill_price is not None else None
        fee = float(order.fee) if order.fee is not None else None
        # 未成交沒有成交價 → 金額一律 null。填 0 會被讀成「這筆不用錢」
        gross = round(float(order.qty) * fill_price, 2) if fill_price is not None else None
        net = (
            round(gross + (fee or 0) if order.side == "buy" else gross - (fee or 0), 2)
            if gross is not None
            else None
        )
        pnl = realized.get(order.id, {})
        out.append(
            {
                "id": order.id,
                "symbol": stock.symbol,
                "name": stock.name,
                "side": order.side,
                "qty": float(order.qty),
                # is not None：美股手續費恆為 0，真值判斷會把它變成 null，
                # 前端就分不清「免手續費」與「尚未計費」
                "fill_price": fill_price,
                "fee": fee,
                # 成交金額（未計費）與淨額：買進為實際支出，賣出為實際入袋
                "gross_amount": gross,
                "net_amount": net,
                "avg_cost": pnl.get("avg_cost"),
                "realized_pnl": pnl.get("realized_pnl"),
                "realized_pnl_pct": pnl.get("realized_pnl_pct"),
                "status": order.status,
                "decided_by": order.decided_by,
                "fill_kind": order.fill_kind,
                "reject_reason": order.reject_reason,
                # 走 as_utc_iso 才會帶 Z：少了時區標記，瀏覽器的 new Date()
                # 會把字串當本地時間解讀，對台灣就整整差 8 小時
                "created_at": as_utc_iso(order.created_at),
                "filled_at": as_utc_iso(order.filled_at),
                "ai_report": json.loads(report.payload_json) if report else None,
            }
        )
    return ok(out)


@router.post("/simulation/{market}:decide", response_model=Envelope)
def trigger_decisions(market: Market) -> Envelope:
    """手動觸發 AI 決策。會先自動對託管股跑當日批次分析（有快取不重複扣額度）。"""
    run_id = enqueue_job(
        f"simulation-decide-{market.lower()}",
        job_type="simulation_decide",
        payload={"market": market},
        idempotency_key=f"simulation-decide:{market}",
    )
    return ok(
        {"started": True, "job": f"simulation-decide-{market.lower()}", "run_id": run_id}
    )


@router.post("/simulation/{market}:fill", response_model=Envelope)
def trigger_fill(market: Market, db: Session = Depends(get_db)) -> Envelope:
    """手動觸發撮合（正式流程於每日資料同步後執行）。"""
    return ok(fill_pending_orders(db, market))
