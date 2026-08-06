"""add next_attempt_at so job retries back off instead of firing immediately

Revision ID: d7f3a1c85be4
Revises: b4d17e0a92c5
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d7f3a1c85be4"
down_revision: str | None = "b4d17e0a92c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL＝可立即領取，既有的 queued 工作因此不受影響（不需要回填）。
    op.add_column("job_runs", sa.Column("next_attempt_at", sa.DateTime()))


def downgrade() -> None:
    op.drop_column("job_runs", "next_attempt_at")
