from datetime import date, datetime, timezone

import app.services as services


def test_market_today_uses_exchange_timezone():
    time_service = getattr(services, "time_service", None)
    assert time_service is not None
    if time_service is None:
        return

    now = datetime(2026, 7, 10, 16, 30, tzinfo=timezone.utc)
    assert time_service.market_today("TW", now) == date(2026, 7, 11)
    assert time_service.market_today("US", now) == date(2026, 7, 10)


def test_naive_utc_timestamp_maps_to_market_date():
    time_service = getattr(services, "time_service", None)
    assert time_service is not None
    if time_service is None:
        return

    created_at = datetime(2026, 7, 10, 16, 30)
    assert time_service.market_date_from_utc(created_at, "TW") == date(2026, 7, 11)
    assert time_service.market_date_from_utc(created_at, "US") == date(2026, 7, 10)
