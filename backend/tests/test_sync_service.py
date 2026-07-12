from datetime import timedelta

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock
from app.providers.market.base import OhlcvRow
from app.services import sync_service
from app.services.time_service import market_today


async def test_sync_refreshes_recent_window_and_updates_existing_prices(monkeypatch):
    db = SessionLocal()
    today = market_today("US")
    try:
        stock = Stock(
            symbol="REVISE", market="US", name="Revision", currency="USD", kind="stock"
        )
        db.add(stock)
        db.commit()
        db.refresh(stock)
        existing_date = today - timedelta(days=1)
        db.add(
            DailyPrice(
                stock_id=stock.id,
                date=existing_date,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            )
        )
        db.commit()
        captured = {}

        async def fake_prices(market, symbol, start, end):
            captured.update({"start": start, "end": end})
            return [
                OhlcvRow(
                    date=existing_date,
                    open=104,
                    high=106,
                    low=103,
                    close=105,
                    volume=2000,
                )
            ]

        monkeypatch.setattr(sync_service.market_data, "get_daily_prices", fake_prices)

        changed = await sync_service.sync_prices(db, stock)

        db.refresh(db.get(DailyPrice, (stock.id, existing_date)))
        row = db.get(DailyPrice, (stock.id, existing_date))
        assert captured["start"] <= existing_date - timedelta(days=9)
        assert float(row.close) == 105
        assert changed == 1
    finally:
        db.close()
