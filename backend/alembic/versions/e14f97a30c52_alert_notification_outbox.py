"""add alert notification outbox state

Revision ID: e14f97a30c52
Revises: d93c2f8756bc
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e14f97a30c52"
down_revision: str | None = "d93c2f8756bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alert_events") as batch:
        batch.add_column(
            sa.Column(
                "notification_status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(
            sa.Column(
                "notification_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("notification_error", sa.Text()))
        batch.add_column(sa.Column("sent_at", sa.DateTime()))
        batch.create_unique_constraint(
            "uq_alert_events_daily", ["alert_id", "trade_date"]
        )


def downgrade() -> None:
    with op.batch_alter_table("alert_events") as batch:
        batch.drop_constraint("uq_alert_events_daily", type_="unique")
        batch.drop_column("sent_at")
        batch.drop_column("notification_error")
        batch.drop_column("notification_attempts")
        batch.drop_column("notification_status")
