"""Exchange-session calendar helpers for Taiwan and US markets."""

from datetime import date, timedelta

import exchange_calendars as xcals

CALENDAR_NAMES = {"TW": "XTAI", "US": "XNYS"}


def next_trading_dates(market: str, after: date, count: int) -> list[date]:
    calendar = xcals.get_calendar(CALENDAR_NAMES[market])
    end = after + timedelta(days=max(30, count * 3))
    sessions = calendar.sessions_in_range(after + timedelta(days=1), end)
    return [timestamp.date() for timestamp in sessions[:count]]
