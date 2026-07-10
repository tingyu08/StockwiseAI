from app.services import alert_service


async def test_notifications_are_optional_when_no_webhook_is_configured():
    sender = getattr(alert_service, "send_alert_notifications", None)
    assert callable(sender)
    if not sender:
        return

    result = await sender([{"symbol": "2330", "kind": "price_above", "value": 1000}], webhook_url="")

    assert result == {"sent": 0, "failed": 0}
