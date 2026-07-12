"""add analysis input hashes and overview prompt versions

Revision ID: d93c2f8756bc
Revises: c82b7401d4ab
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d93c2f8756bc"
down_revision: str | None = "c82b7401d4ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_reports",
        sa.Column("input_hash", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "ai_overviews",
        sa.Column("prompt_version", sa.String(length=16), nullable=False, server_default="v1"),
    )
    op.add_column(
        "ai_overviews",
        sa.Column("input_hash", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("ai_overviews", "input_hash")
    op.drop_column("ai_overviews", "prompt_version")
    op.drop_column("ai_reports", "input_hash")
