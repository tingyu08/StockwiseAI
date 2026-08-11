"""add filling_since lease and reject orders already stuck in 'filling'

Revision ID: f2a6c81d3e94
Revises: e5b91c47af20

撿訂單的查詢全都只看 status == 'pending'，所以一旦撮合中斷、訂單留在
'filling'，就再也沒有任何程式碼會碰它。正式環境有兩筆 00403A 從
2026-07-09、07-10 卡了一個月。

既有的孤兒一律標成 rejected 而非還原成 pending：那些決策是一個月前做的，
拿舊判斷去吃今天的開盤價比不成交更糟。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "f2a6c81d3e94"
down_revision: str | None = "e5b91c47af20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STUCK_REASON = "撮合流程中斷，決策已過期（系統回收）"


def upgrade() -> None:
    op.add_column("sim_orders", sa.Column("filling_since", sa.DateTime()))
    # 升級當下不會有任何撮合在跑（服務尚未啟動），故此處回收是安全的
    op.execute(
        sa.text(
            "UPDATE sim_orders SET status = 'rejected', reject_reason = :reason "
            "WHERE status = 'filling'"
        ).bindparams(reason=STUCK_REASON)
    )


def downgrade() -> None:
    # 被回收的訂單不還原：無從分辨哪些原本就是 rejected，
    # 且還原成 filling 等於把死路重新造出來。
    op.drop_column("sim_orders", "filling_since")
