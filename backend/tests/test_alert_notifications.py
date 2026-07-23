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


async def test_alert_notification_gives_up_after_max_attempts(monkeypatch):
    """webhook 持續失敗達上限 → 標記 failed，不再每輪重撈同一批事件。"""
    deliver = alert_service.deliver_pending_notifications
    db = SessionLocal()
    try:
        stock = Stock(
            symbol="ALRT2", market="TW", name="Alert", currency="TWD", kind="stock"
        )
        db.add(stock)
        db.commit()
        db.refresh(stock)
        db.add(
            DailyPrice(
                stock_id=stock.id, date=date(2026, 7, 10),
                open=100, high=101, low=99, close=100, volume=1000,
            )
        )
        alert = Alert(stock_id=stock.id, kind="price_above", threshold=90)
        db.add(alert)
        db.commit()
        alert_service.check_alerts(db, "TW")
        event = db.query(AlertEvent).filter_by(alert_id=alert.id).one()

        async def always_fail(events, webhook_url=None):
            return {"sent": 0, "failed": len(events)}

        monkeypatch.setattr(alert_service, "send_alert_notifications", always_fail)

        # 連續失敗直到達上限：每輪 +1 次嘗試，第 MAX 次後標記 failed
        for _ in range(alert_service.MAX_NOTIFY_ATTEMPTS):
            await deliver(db, webhook_url="https://example.test/hook")
        db.refresh(event)
        assert event.notification_attempts == alert_service.MAX_NOTIFY_ATTEMPTS
        assert event.notification_status == "failed"

        # 已放棄的事件不再被後續投遞撈起（狀態已非 pending）
        calls = {"n": 0}

        async def count_send(events, webhook_url=None):
            calls["n"] += len(events)
            return {"sent": 0, "failed": len(events)}

        monkeypatch.setattr(alert_service, "send_alert_notifications", count_send)
        result = await deliver(db, webhook_url="https://example.test/hook")
        assert result == {"sent": 0, "failed": 0}
        assert calls["n"] == 0
    finally:
        db.close()
