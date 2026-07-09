"""Database engine and session factory.

pool_recycle/pool_pre_ping are mandatory for Neon free tier: idle connections
must be released so the database can scale to zero (see docs/SD.md §6.2).
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _normalize_url(url: str) -> str:
    """Neon 給的 postgres://、postgresql:// 統一改走 psycopg v3 driver。"""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _build_engine():
    settings = get_settings()
    url = _normalize_url(settings.database_url)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_recycle=300, pool_size=2, max_overflow=3)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
