from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import NotFoundError
from app.models import Stock
from app.models.alert import Alert, AlertEvent

router = APIRouter(tags=["alerts"])

AlertKind = Literal["price_above", "price_below", "premium_above", "premium_below"]

KIND_LABELS = {
    "price_above": "價格高於",
    "price_below": "價格低於",
    "premium_above": "溢價高於",
    "premium_below": "折價低於",
}


class CreateAlertBody(BaseModel):
    market: Literal["TW", "US"]
    symbol: str = Field(min_length=1, max_length=16)
    kind: AlertKind
    threshold: float


@router.get("/alerts", response_model=Envelope)
def list_alerts(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    latest_events = (
        select(
            AlertEvent.alert_id,
            func.max(AlertEvent.trade_date).label("latest_date"),
        )
        .group_by(AlertEvent.alert_id)
        .subquery()
    )
    rows = db.execute(
        select(Alert, Stock, AlertEvent)
        .join(Stock, Alert.stock_id == Stock.id)
        .outerjoin(latest_events, latest_events.c.alert_id == Alert.id)
        .outerjoin(
            AlertEvent,
            and_(
                AlertEvent.alert_id == Alert.id,
                AlertEvent.trade_date == latest_events.c.latest_date,
            ),
        )
        .where(Stock.market == market)
        .order_by(Alert.created_at.desc())
    ).all()
    out = []
    for alert, stock, last_event in rows:
        out.append(
            {
                "id": alert.id,
                "symbol": stock.symbol,
                "name": stock.name,
                "kind": alert.kind,
                "kind_label": KIND_LABELS[alert.kind],
                "threshold": float(alert.threshold),
                "active": alert.active,
                "last_triggered": {
                    "date": last_event.trade_date.isoformat(),
                    "value": float(last_event.value),
                }
                if last_event
                else None,
            }
        )
    return ok(out)


@router.post("/alerts", response_model=Envelope)
def create_alert(body: CreateAlertBody, db: Session = Depends(get_db)) -> Envelope:
    stock = db.execute(
        select(Stock).where(Stock.market == body.market, Stock.symbol == body.symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"尚未追蹤 {body.market}/{body.symbol}，請先加入自選")
    if body.kind.startswith("premium") and stock.kind != "etf":
        raise NotFoundError(f"{body.symbol} 不是 ETF，無法設定折溢價警示")
    alert = Alert(stock_id=stock.id, kind=body.kind, threshold=body.threshold)
    db.add(alert)
    db.commit()
    return ok({"id": alert.id, "symbol": stock.symbol, "kind": body.kind, "threshold": body.threshold})


@router.delete("/alerts/{alert_id}", response_model=Envelope)
def delete_alert(alert_id: int, db: Session = Depends(get_db)) -> Envelope:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError("查無此警示")
    for event in db.execute(
        select(AlertEvent).where(AlertEvent.alert_id == alert_id)
    ).scalars().all():
        db.delete(event)
    db.delete(alert)
    db.commit()
    return ok({"deleted": alert_id})
