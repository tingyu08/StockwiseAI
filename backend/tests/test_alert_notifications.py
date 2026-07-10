from app.services import alert_service
from datetime import date

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock
from app.models.alert import Alert, AlertEvent


async def test_notifications_are_optional_when_no_webhook_is_configured():
    sender = getattr(alert_service, "send_alert_notifications", None)
    assert callable(sender)
    if not sender:
        return

    result = await sender([{"symbol": "2330", "kind": "price_above", "value": 1000}], webhook_url="")

    assert result == {"sent": 0, "failed": 0}


async def test_failed_alert_notification_remains_pending_for_retry(monkeypatch):
    deliver = getattr(alert_service, "deliver_pending_notifications", None)
    assert callable(deliver)
    if not deliver:
        return

    db = SessionLocal()
    try:
        stock = Stock(
            symbol="ALRT1", market="TW", name="Alert", currency="TWD", kind="stock"
        )
        db.add(stock)
        db.commit()
        db.refresh(stock)
        db.add(
            DailyPrice(
                stock_id=stock.id,
                date=date(2026, 7, 10),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            )
        )
        alert = Alert(stock_id=stock.id, kind="price_above", threshold=90)
        db.add(alert)
        db.commit()
        alert_service.check_alerts(db, "TW")
        event = db.query(AlertEvent).filter_by(alert_id=alert.id).one()

        outcomes = iter(
            [{"sent": 0, "failed": 1}, {"sent": 1, "failed": 0}]
        )

        async def fake_send(events, webhook_url=None):
            return next(outcomes)

        monkeypatch.setattr(alert_service, "send_alert_notifications", fake_send)

        await deliver(db, webhook_url="https://example.test/hook")
        db.refresh(event)
        assert event.notification_status == "pending"
        assert event.notification_attempts == 1

        await deliver(db, webhook_url="https://example.test/hook")
        db.refresh(event)
        assert event.notification_status == "sent"
        assert event.notification_attempts == 2
        assert event.sent_at is not None
    finally:
        db.close()
