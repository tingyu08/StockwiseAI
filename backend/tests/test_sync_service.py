import asyncio
import threading
from datetime import timedelta

from sqlalchemy import event

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

        changed = await sync_service.sync_prices(stock.id, stock.market, stock.symbol)

        db.refresh(db.get(DailyPrice, (stock.id, existing_date)))
        row = db.get(DailyPrice, (stock.id, existing_date))
        assert captured["start"] <= existing_date - timedelta(days=9)
        assert float(row.close) == 105
        assert changed == 1
    finally:
        db.close()


async def test_sync_persistence_runs_off_the_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    persisted_on = None

    monkeypatch.setattr(sync_service, "_load_last_price_date", lambda _stock_id: None)

    async def fake_prices(_market, _symbol, _start, _end):
        return []

    def fake_persist(_stock_id, _rows):
        nonlocal persisted_on
        persisted_on = threading.get_ident()
        return 0

    monkeypatch.setattr(sync_service.market_data, "get_daily_prices", fake_prices)
    monkeypatch.setattr(sync_service, "_persist_price_rows", fake_persist)

    assert await sync_service.sync_prices(1, "TW", "2330") == 0
    assert persisted_on is not None
    assert persisted_on != main_thread


def test_persist_price_rows_loads_existing_prices_in_one_query(monkeypatch):
    db = SessionLocal()
    try:
        stock = Stock(symbol="BATCH", market="US", name="Batch", currency="USD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        rows = [
            OhlcvRow(date=market_today("US") - timedelta(days=index), open=10, high=11, low=9, close=10, volume=100)
            for index in range(3)
        ]
        monkeypatch.setattr(sync_service, "_recompute_indicators", lambda *_args: None)
        selects = []

        def count_select(_conn, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT") and "daily_prices" in statement:
                selects.append(statement)

        event.listen(sync_service.engine, "before_cursor_execute", count_select)
        try:
            assert sync_service._persist_price_rows(stock.id, rows) == 3
        finally:
            event.remove(sync_service.engine, "before_cursor_execute", count_select)
        assert len(selects) == 1
    finally:
        db.close()
