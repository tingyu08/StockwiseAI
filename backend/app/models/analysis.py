from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AiReport(Base):
    __tablename__ = "ai_reports"
    __table_args__ = (
        # 資料庫層保證「同一檔、同一交易日、同一種報告」只有一份（當日快取）
        UniqueConstraint("stock_id", "trade_date", "kind", name="uq_ai_reports_daily"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(16))
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(8))  # 'routine' | 'deep' | 'news'
    action: Mapped[str | None] = mapped_column(String(4))  # 'buy' | 'sell' | 'hold'
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    payload_json: Mapped[str] = mapped_column(Text)  # 完整結構化報告
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)  # 5 | 20
    method: Mapped[str] = mapped_column(String(32))  # 'regression' | 'prophet' | 'ai'
    predicted_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiOverview(Base):
    """整體自選股的 AI 總評（每市場每交易日一份）。"""

    __tablename__ = "ai_overviews"
    __table_args__ = (UniqueConstraint("market", "trade_date", name="uq_overview_daily"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(2))
    trade_date: Mapped[date] = mapped_column(Date)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(16), default="v1")
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiUsageLog(Base):
    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AiQuotaReservation(Base):
    """In-flight AI request counted before the provider call is sent."""

    __tablename__ = "ai_quota_reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(64), index=True)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
