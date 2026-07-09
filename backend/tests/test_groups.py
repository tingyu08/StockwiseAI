from datetime import date, timedelta

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock, WatchlistItem


def _seed_watch(db, symbol, market="TW"):
    stock = Stock(symbol=symbol, market=market, name=f"G{symbol}", currency="TWD", kind="stock")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    db.add(DailyPrice(stock_id=stock.id, date=date.today() - timedelta(days=1),
                      open=100, high=101, low=99, close=100, volume=1))
    db.add(WatchlistItem(stock_id=stock.id, sort_order=0))
    db.commit()
    return stock


def test_group_crud_and_assignment(client):
    db = SessionLocal()
    try:
        _seed_watch(db, "6001")
        _seed_watch(db, "6002")
    finally:
        db.close()

    # 建立群組
    res = client.post("/api/v1/groups", json={"market": "TW", "name": "半導體"})
    assert res.status_code == 200
    gid = res.json()["data"]["id"]

    # 重名 409
    res = client.post("/api/v1/groups", json={"market": "TW", "name": "半導體"})
    assert res.status_code == 409

    # 指派群組
    res = client.patch(f"/api/v1/watchlist/6001?market=TW", json={"group_id": gid})
    assert res.json()["data"]["group_id"] == gid

    # 排序
    res = client.put("/api/v1/watchlist/reorder", json={
        "market": "TW",
        "items": [
            {"symbol": "6002", "group_id": gid, "sort_order": 0},
            {"symbol": "6001", "group_id": None, "sort_order": 1},
        ],
    })
    assert res.json()["data"]["updated"] == 2

    rows = client.get("/api/v1/watchlist?market=TW").json()["data"]
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["6002"]["group_id"] == gid
    assert by_symbol["6001"]["group_id"] is None
    # 排序生效：6002 在 6001 前
    symbols = [r["symbol"] for r in rows if r["symbol"] in ("6001", "6002")]
    assert symbols == ["6002", "6001"]

    # 刪除群組：股票移回未分組
    res = client.delete(f"/api/v1/groups/{gid}")
    assert res.status_code == 200
    rows = client.get("/api/v1/watchlist?market=TW").json()["data"]
    assert all(r["group_id"] is None for r in rows)


def test_overview_404_when_none(client):
    res = client.get("/api/v1/analysis/overview?market=US")
    assert res.status_code == 404
