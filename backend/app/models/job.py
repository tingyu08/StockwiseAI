from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_status_created", "status", "created_at"),
        Index(
            "uq_job_runs_active_idempotency",
            "idempotency_key",
            unique=True,
            sqlite_where=text(
                "idempotency_key IS NOT NULL AND status IN ('queued', 'running')"
            ),
            postgresql_where=text(
                "idempotency_key IS NOT NULL AND status IN ('queued', 'running')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    job_type: Mapped[str] = mapped_column(String(32), default="scheduled")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
