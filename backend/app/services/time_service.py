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


def as_utc_iso(value: datetime | None) -> str | None:
    """把「naive 但語意為 UTC」的欄位輸出成帶時區的 ISO 字串。

    DB 的 created_at 一律是 naive UTC（見 utc_now_naive）。直接 isoformat()
    會產出沒有時區標記的字串，瀏覽器的 new Date() 會把它當「當地時間」解讀
    ——對台灣就是整整差 8 小時。凡是要拿到前端顯示時刻的欄位都必須走這裡。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def market_date_from_utc(value: datetime, market: str) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MARKET_TIMEZONES[market]).date()
