"""add hot-path composite indexes

Revision ID: d824a133f070
Revises: c31fcb2c9d50
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d824a133f070"
down_revision: str | None = "c31fcb2c9d50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_sim_orders_account_status_created",
        "sim_orders",
        ["account_id", "status", "created_at"],
    )
    op.create_index(
        "ix_alert_events_alert_trade_date",
        "alert_events",
        ["alert_id", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_events_alert_trade_date", table_name="alert_events")
    op.drop_index("ix_sim_orders_account_status_created", table_name="sim_orders")
