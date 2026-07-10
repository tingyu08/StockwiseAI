"""extend job runs into a recoverable database queue

Revision ID: c82b7401d4ab
Revises: b71a4e92c013
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "c82b7401d4ab"
down_revision: str | None = "b71a4e92c013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("job_type", sa.String(length=32), nullable=False, server_default="scheduled"),
    )
    op.add_column(
        "job_runs",
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column("job_runs", sa.Column("idempotency_key", sa.String(length=160)))
    op.add_column(
        "job_runs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
    )
    op.add_column("job_runs", sa.Column("heartbeat_at", sa.DateTime()))
    op.add_column("job_runs", sa.Column("lease_expires_at", sa.DateTime()))
    op.create_index(
        "ix_job_runs_status_created", "job_runs", ["status", "created_at"]
    )
    active = sa.text("idempotency_key IS NOT NULL AND status IN ('queued', 'running')")
    op.create_index(
        "uq_job_runs_active_idempotency",
        "job_runs",
        ["idempotency_key"],
        unique=True,
        sqlite_where=active,
        postgresql_where=active,
    )


def downgrade() -> None:
    op.drop_index("uq_job_runs_active_idempotency", table_name="job_runs")
    op.drop_index("ix_job_runs_status_created", table_name="job_runs")
    op.drop_column("job_runs", "lease_expires_at")
    op.drop_column("job_runs", "heartbeat_at")
    op.drop_column("job_runs", "max_attempts")
    op.drop_column("job_runs", "idempotency_key")
    op.drop_column("job_runs", "payload_json")
    op.drop_column("job_runs", "job_type")
