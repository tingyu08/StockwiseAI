"""Exchange-session calendar helpers for Taiwan and US markets."""

from datetime import date, timedelta

import exchange_calendars as xcals

CALENDAR_NAMES = {"TW": "XTAI", "US": "XNYS"}


def _calendar(market: str):
    return xcals.get_calendar(CALENDAR_NAMES[market])


def next_trading_dates(market: str, after: date, count: int) -> list[date]:
    calendar = _calendar(market)
    end = after + timedelta(days=max(30, count * 3))
    sessions = calendar.sessions_in_range(after + timedelta(days=1), end)
    return [timestamp.date() for timestamp in sessions[:count]]


def is_trading_day(market: str, day: date) -> bool:
    """該市場當地日期是否為交易日（假日/週末閘門用）。"""
    return bool(_calendar(market).is_session(day.isoformat()))


def last_trading_session(market: str, on_or_before: date) -> date:
    """不晚於指定日期的最近一個交易日（決策端價格新鮮度檢查用）。"""
    calendar = _calendar(market)
    return calendar.date_to_session(on_or_before.isoformat(), direction="previous").date()
