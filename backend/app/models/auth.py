from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_single_owner",
            "is_owner",
            unique=True,
            sqlite_where=text("is_owner = 1"),
            postgresql_where=text("is_owner = true"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    username_normalized: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
