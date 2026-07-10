from datetime import date
import importlib


def test_us_prediction_dates_skip_exchange_holiday():
    try:
        calendar = importlib.import_module("app.services.trading_calendar")
    except ModuleNotFoundError:
        calendar = None
    next_dates = getattr(calendar, "next_trading_dates", None)
    assert callable(next_dates)
    if not next_dates:
        return

    dates = next_dates("US", date(2026, 7, 2), 2)

    assert dates == [date(2026, 7, 6), date(2026, 7, 7)]
