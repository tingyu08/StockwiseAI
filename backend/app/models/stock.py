from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = (UniqueConstraint("market", "symbol", name="uq_stocks_market_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    market: Mapped[str] = mapped_column(String(2))  # 'TW' | 'US'
    name: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))  # 'TWD' | 'USD'
    kind: Mapped[str] = mapped_column(String(8))  # 'stock' | 'etf'


class DailyPrice(Base):
    __tablename__ = "daily_prices"

    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Numeric(16, 4))
    high: Mapped[float | None] = mapped_column(Numeric(16, 4))
    low: Mapped[float | None] = mapped_column(Numeric(16, 4))
    close: Mapped[float | None] = mapped_column(Numeric(16, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)


class EtfNav(Base):
    __tablename__ = "etf_nav"

    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav: Mapped[float | None] = mapped_column(Numeric(16, 4))
    close: Mapped[float | None] = mapped_column(Numeric(16, 4))
    premium_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))  # (close-nav)/nav*100


class Indicator(Base):
    __tablename__ = "indicators"

    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    ma5: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ma20: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ma60: Mapped[float | None] = mapped_column(Numeric(16, 4))
    rsi14: Mapped[float | None] = mapped_column(Numeric(8, 4))
    kd_k: Mapped[float | None] = mapped_column(Numeric(8, 4))
    kd_d: Mapped[float | None] = mapped_column(Numeric(8, 4))
    macd: Mapped[float | None] = mapped_column(Numeric(16, 6))
    macd_signal: Mapped[float | None] = mapped_column(Numeric(16, 6))
    bb_upper: Mapped[float | None] = mapped_column(Numeric(16, 4))
    bb_lower: Mapped[float | None] = mapped_column(Numeric(16, 4))
