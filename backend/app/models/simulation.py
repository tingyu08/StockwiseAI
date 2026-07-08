from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SimAccount(Base):
    __tablename__ = "sim_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(2), unique=True)  # 台股/美股各一
    currency: Mapped[str] = mapped_column(String(3))
    initial_cash: Mapped[float] = mapped_column(Numeric(16, 2))
    cash: Mapped[float] = mapped_column(Numeric(16, 2))


class SimOrder(Base):
    """事件溯源：持倉與權益曲線由 orders 重放推導，不可變更既有紀錄。"""

    __tablename__ = "sim_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sim_accounts.id"), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    side: Mapped[str] = mapped_column(String(4))  # 'buy' | 'sell'
    qty: Mapped[float] = mapped_column(Numeric(16, 4))  # 美股可小數股
    fill_price: Mapped[float | None] = mapped_column(Numeric(16, 4))
    fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(8), default="pending")  # pending|filled|rejected
    decided_by: Mapped[str] = mapped_column(String(4))  # 'ai' | 'user'
    ai_report_id: Mapped[int | None] = mapped_column(ForeignKey("ai_reports.id"))
    reject_reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    filled_at: Mapped[datetime | None] = mapped_column(DateTime)
