"""add owner authentication

Revision ID: a6c1e58d734b
Revises: f25a8b1c9d02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c1e58d734b"
down_revision: str | None = "f25a8b1c9d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("username_normalized", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username_normalized", "users", ["username_normalized"], unique=True)
    op.create_index("uq_users_single_owner", "users", ["is_owner"], unique=True,
                    postgresql_where=sa.text("is_owner = true"), sqlite_where=sa.text("is_owner = 1"))
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_table("users")
