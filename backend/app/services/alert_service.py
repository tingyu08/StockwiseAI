"""警示檢查：每日資料同步後執行，同一警示同一交易日只觸發一次。"""
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import DailyPrice, EtfNav, Stock
from app.models.alert import Alert, AlertEvent
from app.services.time_service import utc_now_naive

logger = logging.getLogger(__name__)

# webhook 連續失敗達此次數即標記 failed，停止每輪重撈同一批事件
MAX_NOTIFY_ATTEMPTS = 5


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
        event = AlertEvent(alert_id=alert.id, trade_date=trade_date, value=value)
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        triggered += 1
        events.append({
            "event_id": event.id,
            "alert_id": alert.id,
            "symbol": stock.symbol,
            "name": stock.name,
            "kind": alert.kind,
            "threshold": float(alert.threshold),
            "value": value,
            "trade_date": trade_date.isoformat(),
        })
        logger.info("alert triggered: %s %s %s (value=%.4f)", stock.symbol, alert.kind, alert.threshold, value)
    return {"market": market, "checked": len(alerts), "triggered": triggered, "events": events}


async def deliver_pending_notifications(
    db: Session, webhook_url: str | None = None
) -> dict:
    """Deliver the alert outbox; failed events remain pending for later runs."""
    rows = db.execute(
        select(AlertEvent, Alert, Stock)
        .join(Alert, AlertEvent.alert_id == Alert.id)
        .join(Stock, Alert.stock_id == Stock.id)
        .where(AlertEvent.notification_status == "pending")
        .order_by(AlertEvent.created_at, AlertEvent.id)
        .limit(100)
    ).all()
    if not rows:
        return {"sent": 0, "failed": 0}
    payload = [
        {
            "event_id": event.id,
            "alert_id": alert.id,
            "symbol": stock.symbol,
            "name": stock.name,
            "kind": alert.kind,
            "threshold": float(alert.threshold),
            "value": float(event.value),
            "trade_date": event.trade_date.isoformat(),
        }
        for event, alert, stock in rows
    ]
    result = await send_alert_notifications(payload, webhook_url=webhook_url)
    succeeded = result["failed"] == 0
    for event, _alert, _stock in rows:
        event.notification_attempts += 1
        if succeeded:
            event.notification_status = "sent"
            event.notification_error = None
            event.sent_at = utc_now_naive()
        else:
            event.notification_error = "webhook delivery failed"
            if event.notification_attempts >= MAX_NOTIFY_ATTEMPTS:
                # 達重試上限 → 標記 failed，讓壞掉的 webhook 不再每輪
                # 重撈同一批事件而卡住後續告警的投遞名額
                event.notification_status = "failed"
    db.commit()
    return result


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
