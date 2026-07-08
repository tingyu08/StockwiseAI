from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WatchlistItem(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("stock_id", name="uq_watchlist_stock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    ai_managed: Mapped[bool] = mapped_column(default=False)  # 是否交給 AI 模擬操作
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
