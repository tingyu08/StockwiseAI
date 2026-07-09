from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WatchGroup(Base):
    """自選股自訂群組（依市場隔離）。"""

    __tablename__ = "watch_groups"
    __table_args__ = (UniqueConstraint("market", "name", name="uq_group_market_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(2))
    name: Mapped[str] = mapped_column(String(32))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WatchlistItem(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("stock_id", name="uq_watchlist_stock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("watch_groups.id"), default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    ai_managed: Mapped[bool] = mapped_column(default=False)  # 是否交給 AI 模擬操作
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
