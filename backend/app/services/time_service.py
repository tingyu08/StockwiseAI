"""Explicit timezone boundaries for market dates and UTC persistence."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

MARKET_TIMEZONES = {
    "TW": ZoneInfo("Asia/Taipei"),
    "US": ZoneInfo("America/New_York"),
}


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def market_today(market: str, now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(MARKET_TIMEZONES[market]).date()


def market_date_from_utc(value: datetime, market: str) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MARKET_TIMEZONES[market]).date()
