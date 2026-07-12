"""add in-flight AI quota reservations

Revision ID: b71a4e92c013
Revises: e910cb2743f1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b71a4e92c013"
down_revision: str | None = "e910cb2743f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_quota_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_ai_quota_reservations_model", "ai_quota_reservations", ["model"]
    )
    op.create_index(
        "ix_ai_quota_reservations_created_at",
        "ai_quota_reservations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_quota_reservations_created_at", table_name="ai_quota_reservations"
    )
    op.drop_index(
        "ix_ai_quota_reservations_model", table_name="ai_quota_reservations"
    )
    op.drop_table("ai_quota_reservations")
