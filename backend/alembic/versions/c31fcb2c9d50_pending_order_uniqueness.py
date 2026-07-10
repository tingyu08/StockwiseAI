"""prevent duplicate pending simulation orders

Revision ID: c31fcb2c9d50
Revises: af0e53ed521e
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "c31fcb2c9d50"
down_revision: str | None = "af0e53ed521e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_sim_orders_pending_account_stock",
        "sim_orders",
        ["account_id", "stock_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_sim_orders_pending_account_stock", table_name="sim_orders")
