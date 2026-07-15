from sqlalchemy import select

from app.api.v1 import watchlist
from app.core.db import SessionLocal
from app.models import Stock, WatchlistItem


def _stock(symbol: str) -> Stock:
    db = SessionLocal()
    try:
        stock = Stock(symbol=symbol, market="TW", name=symbol, currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        db.expunge(stock)
        return stock
    finally:
        db.close()


def test_add_watch_enqueues_sync_without_waiting_for_prices(client, monkeypatch):
    stock = _stock("QADD1")
    queued = {}

    async def fake_ensure(_db, _market, _symbol):
        return stock

    def fake_enqueue(name, **kwargs):
        queued.update({"name": name, **kwargs})
        return 321

    monkeypatch.setattr(watchlist, "ensure_stock", fake_ensure)
    monkeypatch.setattr(watchlist, "enqueue_job", fake_enqueue)

    response = client.post("/api/v1/watchlist", json={"market": "TW", "symbol": "QADD1"})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "symbol": "QADD1",
        "market": "TW",
        "name": "QADD1",
        "started": True,
        "job": "sync-tw-qadd1",
        "run_id": 321,
    }
    assert queued["job_type"] == "stock_sync"
    assert queued["idempotency_key"] == "stock-sync:TW:QADD1"
    with SessionLocal() as db:
        assert db.scalar(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))


def test_add_watch_survives_sync_enqueue_failure(client, monkeypatch):
    stock = _stock("QADD2")

    async def fake_ensure(_db, _market, _symbol):
        return stock

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(watchlist, "ensure_stock", fake_ensure)
    monkeypatch.setattr(watchlist, "enqueue_job", fail_enqueue)

    response = client.post("/api/v1/watchlist", json={"market": "TW", "symbol": "QADD2"})

    assert response.status_code == 200
    assert response.json()["data"]["started"] is False
    assert response.json()["data"]["run_id"] is None
    with SessionLocal() as db:
        assert db.scalar(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
