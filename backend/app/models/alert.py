from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ALERT_KINDS = ("price_above", "price_below", "premium_above", "premium_below")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # ALERT_KINDS
    threshold: Mapped[float] = mapped_column(Numeric(16, 4))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric(16, 4))  # 觸發當下的價格/折溢價
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
