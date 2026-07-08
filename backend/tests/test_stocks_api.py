from datetime import date, timedelta

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock


def seed_stock(symbol="2330", market="TW", days=30) -> None:
    db = SessionLocal()
    try:
        stock = Stock(symbol=symbol, market=market, name="測試股", currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        for i in range(days):
            db.add(
                DailyPrice(
                    stock_id=stock.id,
                    date=date.today() - timedelta(days=days - i),
                    open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=1000,
                )
            )
        db.commit()
    finally:
        db.close()


def test_prices_of_unknown_stock_returns_404(client):
    res = client.get("/api/v1/stocks/9999/prices", params={"market": "TW"})
    assert res.status_code == 404
    assert res.json()["success"] is False


def test_prices_returns_series(client):
    seed_stock()
    res = client.get("/api/v1/stocks/2330/prices", params={"market": "TW", "range": "3m"})
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"]["stock"]["symbol"] == "2330"
    assert len(body["data"]["series"]) == 30
    first = body["data"]["series"][0]
    assert {"date", "open", "high", "low", "close", "volume", "ma5"} <= set(first)


def test_market_isolation(client):
    # TW 的 2330 不應出現在 US 市場查詢
    res = client.get("/api/v1/stocks/2330/prices", params={"market": "US"})
    assert res.status_code == 404


def test_invalid_market_rejected(client):
    res = client.get("/api/v1/stocks", params={"market": "JP", "q": "sony"})
    assert res.status_code == 422
