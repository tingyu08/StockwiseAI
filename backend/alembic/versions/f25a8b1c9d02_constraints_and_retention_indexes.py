"""add prediction identity and notification queue indexes

Revision ID: f25a8b1c9d02
Revises: e14f97a30c52
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f25a8b1c9d02"
down_revision: str | None = "e14f97a30c52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing installations may contain duplicate forecasts from repeated runs.
    # Keep the newest row before making the logical identity enforceable.
    op.execute(
        """
        DELETE FROM predictions
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM predictions
            GROUP BY stock_id, trade_date, horizon_days, method
        )
        """
    )
    with op.batch_alter_table("predictions") as batch:
        batch.create_unique_constraint(
            "uq_predictions_identity",
            ["stock_id", "trade_date", "horizon_days", "method"],
        )
        batch.create_index("ix_predictions_trade_date", ["trade_date"])
    op.create_index(
        "ix_alert_events_notification_created",
        "alert_events",
        ["notification_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_events_notification_created", table_name="alert_events"
    )
    with op.batch_alter_table("predictions") as batch:
        batch.drop_index("ix_predictions_trade_date")
        batch.drop_constraint("uq_predictions_identity", type_="unique")
