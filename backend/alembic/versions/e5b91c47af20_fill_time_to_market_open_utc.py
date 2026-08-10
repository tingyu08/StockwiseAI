"""restamp open-fill timestamps from local midnight to market open in UTC

Revision ID: e5b91c47af20
Revises: d7f3a1c85be4

engine 原本寫 `datetime.combine(交易日, 00:00)`，既不是 UTC 也不是明確的
當地時刻；同一欄位裡 sentinel 寫的卻是真正的 UTC。統一為 naive UTC 後，
既有資料若原封不動被當成 UTC 解讀，美股會整整差一天：
    DB 2026-07-15 00:00 → 畫面 2026-07-14 20:00（America/New_York）

只改開盤成交（fill_kind IS NULL）。盤中出場（stop_loss / take_profit）
本來就寫 utc_now_naive()，已是正確的 UTC，不得碰。
"""

from collections.abc import Sequence
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa

revision: str = "e5b91c47af20"
down_revision: str | None = "d7f3a1c85be4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MARKET_TIMEZONES = {
    "TW": ZoneInfo("Asia/Taipei"),
    "US": ZoneInfo("America/New_York"),
}
MARKET_OPEN = {"TW": (9, 0), "US": (9, 30)}


def _shift(session_day, market: str, to_open: bool) -> datetime:
    """升級：交易日 → 當地開盤時刻換算的 naive UTC。

    降級是還原成舊語意，而舊值是字面上的 `combine(交易日, 00:00)`——
    它從來不是「當地午夜換算的 UTC」，所以這裡不可再做時區換算，
    否則 07-15 00:00 會被還原成 07-14 16:00。
    """
    if not to_open:
        return datetime.combine(session_day, time(0, 0))
    hour, minute = MARKET_OPEN[market]
    local = datetime.combine(session_day, time(hour, minute)).replace(
        tzinfo=MARKET_TIMEZONES[market]
    )
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _restamp(to_open: bool) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT o.id, o.filled_at, a.market "
            "FROM sim_orders o JOIN sim_accounts a ON a.id = o.account_id "
            "WHERE o.status = 'filled' AND o.fill_kind IS NULL "
            "AND o.filled_at IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        filled_at = row.filled_at
        if isinstance(filled_at, str):  # SQLite 回字串
            filled_at = datetime.fromisoformat(filled_at)
        market = row.market
        if market not in MARKET_TIMEZONES:
            continue
        # 交易日取自「原本的語意」：升級時欄位是當地午夜，日期即交易日；
        # 降級時是當地開盤的 UTC，換回當地後日期同樣是交易日。
        session_day = (
            filled_at.date()
            if to_open
            else filled_at.replace(tzinfo=timezone.utc)
            .astimezone(MARKET_TIMEZONES[market])
            .date()
        )
        conn.execute(
            sa.text("UPDATE sim_orders SET filled_at = :ts WHERE id = :id"),
            {"ts": _shift(session_day, market, to_open), "id": row.id},
        )


def upgrade() -> None:
    _restamp(to_open=True)


def downgrade() -> None:
    _restamp(to_open=False)
