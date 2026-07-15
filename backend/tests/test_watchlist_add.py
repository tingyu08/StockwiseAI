from sqlalchemy import select

from app.api.v1 import watchlist
from app.core.db import SessionLocal
from app.models import JobRun, Stock, WatchlistItem


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

    async def fake_ensure(_db, _market, _symbol):
        return stock

    monkeypatch.setattr(watchlist, "ensure_stock", fake_ensure)

    response = client.post("/api/v1/watchlist", json={"market": "TW", "symbol": "QADD1"})
    repeated = client.post("/api/v1/watchlist", json={"market": "TW", "symbol": "QADD1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["symbol"] == "QADD1"
    assert data["market"] == "TW"
    assert data["name"] == "QADD1"
    assert data["started"] is True
    assert data["job"] == "sync-tw-qadd1"
    assert repeated.status_code == 200
    assert repeated.json()["data"]["run_id"] == data["run_id"]
    with SessionLocal() as db:
        assert db.scalar(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
        run = db.get(JobRun, data["run_id"])
        assert run is not None
        assert run.job_type == "stock_sync"
        assert run.idempotency_key == "stock-sync:TW:QADD1"
        assert run.max_attempts == 3


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
