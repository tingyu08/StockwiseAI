"""警示檢查：每日資料同步後執行，同一警示同一交易日只觸發一次。"""
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import DailyPrice, EtfNav, Stock
from app.models.alert import Alert, AlertEvent

logger = logging.getLogger(__name__)


def check_alerts(db: Session, market: str) -> dict:
    alerts = db.execute(
        select(Alert, Stock)
        .join(Stock, Alert.stock_id == Stock.id)
        .where(Stock.market == market, Alert.active.is_(True))
    ).all()

    triggered = 0
    events = []
    for alert, stock in alerts:
        value, trade_date = _current_value(db, alert, stock)
        if value is None:
            continue
        if not _condition_met(alert.kind, value, float(alert.threshold)):
            continue
        exists = db.execute(
            select(AlertEvent.id).where(
                AlertEvent.alert_id == alert.id, AlertEvent.trade_date == trade_date
            )
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(AlertEvent(alert_id=alert.id, trade_date=trade_date, value=value))
        triggered += 1
        events.append({
            "alert_id": alert.id,
            "symbol": stock.symbol,
            "name": stock.name,
            "kind": alert.kind,
            "threshold": float(alert.threshold),
            "value": value,
            "trade_date": trade_date.isoformat(),
        })
        logger.info("alert triggered: %s %s %s (value=%.4f)", stock.symbol, alert.kind, alert.threshold, value)
    db.commit()
    return {"market": market, "checked": len(alerts), "triggered": triggered, "events": events}


async def send_alert_notifications(
    events: list[dict], webhook_url: str | None = None
) -> dict:
    url = webhook_url if webhook_url is not None else get_settings().alert_webhook_url
    if not url or not events:
        return {"sent": 0, "failed": 0}
    text = "\n".join(
        f"{event['symbol']} {event['kind']}：{event['value']}（門檻 {event['threshold']}）"
        for event in events
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json={"text": text, "events": events})
            response.raise_for_status()
        return {"sent": len(events), "failed": 0}
    except Exception:
        logger.exception("alert webhook delivery failed")
        return {"sent": 0, "failed": len(events)}


def _current_value(db: Session, alert: Alert, stock: Stock):
    if alert.kind.startswith("price"):
        row = db.execute(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id, DailyPrice.close.is_not(None))
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return (float(row.close), row.date) if row else (None, None)
    row = db.execute(
        select(EtfNav)
        .where(EtfNav.stock_id == stock.id, EtfNav.premium_pct.is_not(None))
        .order_by(EtfNav.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (float(row.premium_pct), row.date) if row else (None, None)


def _condition_met(kind: str, value: float, threshold: float) -> bool:
    if kind.endswith("above"):
        return value >= threshold
    return value <= threshold
