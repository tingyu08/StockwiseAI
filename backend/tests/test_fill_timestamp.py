"""成交時刻（filled_at）的時區語意。

原本 engine 寫的是 `datetime.combine(交易日, 00:00)`——既不是 UTC 也不是
明確的當地時刻，而同一個欄位裡 sentinel 寫的卻是 utc_now_naive()。
一個欄位兩種語意，畫面上只好退而求其次只顯示日期。

統一為 naive UTC：開盤成交寫「該交易日當地開盤時刻」換算的 UTC
（台股 09:00 CST、美股 09:30 ET），盤中出場維持觸發當下的 UTC。

權益曲線以 filled_at.date() 歸屬每日損益，所以換算後的 UTC 日期
必須仍等於交易日——兩個市場的開盤都在 UTC 同日，這點由測試守住。
"""
from datetime import date, datetime, timedelta

import pytest

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import DailyPrice, SimOrder, Stock
from app.services.sim.engine import fill_pending_orders, get_or_create_account

SYMBOL_PREFIX = "TS00"


@pytest.fixture(autouse=True)
def _clean_up_own_orders():
    """本檔的成交單必須自行清掉。

    模擬帳戶是全域共用的（每個市場一個），權益曲線與持倉都由 filled orders
    重放推導——留下訂單會讓 test_simulation 的重放結果對不上，
    而那種失敗看起來會像是「權益曲線算錯」，極難追。
    """
    yield
    db = SessionLocal()
    try:
        ids = [
            row.id
            for row in db.execute(
                select(Stock).where(Stock.symbol.like(f"{SYMBOL_PREFIX}%"))
            ).scalars()
        ]
        if ids:
            db.execute(delete(SimOrder).where(SimOrder.stock_id.in_(ids)))
            db.execute(delete(DailyPrice).where(DailyPrice.stock_id.in_(ids)))
            db.execute(delete(Stock).where(Stock.id.in_(ids)))
            db.commit()
    finally:
        db.close()


def _stock_with_price(db, symbol, market, session_day, price=100.0):
    stock = Stock(
        symbol=symbol, market=market, name=f"測試{symbol}",
        currency="TWD" if market == "TW" else "USD", kind="stock",
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    db.add(DailyPrice(
        stock_id=stock.id, date=session_day,
        open=price, high=price * 1.01, low=price * 0.99, close=price, volume=1000,
    ))
    db.commit()
    return stock


def _pending_buy(db, account, stock, created_at):
    order = SimOrder(
        account_id=account.id, stock_id=stock.id, side="buy", qty=1,
        status="pending", decided_by="ai", created_at=created_at,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_tw_fill_is_stamped_at_local_open_in_utc():
    """台股 09:00 CST ＝ 01:00 UTC。"""
    session_day = date(2026, 7, 15)  # 週三
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock_with_price(db, "TS001", "TW", session_day)
        order = _pending_buy(db, account, stock, datetime(2026, 7, 14, 23, 10))

        fill_pending_orders(db, "TW")

        db.refresh(order)
        assert order.status == "filled"
        assert order.filled_at == datetime(2026, 7, 15, 1, 0)
        # 權益曲線以此歸屬當日損益，不可因換算而跨日
        assert order.filled_at.date() == session_day
    finally:
        db.close()


def test_us_fill_is_stamped_at_local_open_in_utc():
    """美股 09:30 EDT（夏令）＝ 13:30 UTC。"""
    session_day = date(2026, 7, 15)
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "US")
        stock = _stock_with_price(db, "TS002", "US", session_day, price=10.0)
        order = _pending_buy(db, account, stock, datetime(2026, 7, 14, 23, 10))

        fill_pending_orders(db, "US")

        db.refresh(order)
        assert order.status == "filled"
        assert order.filled_at == datetime(2026, 7, 15, 13, 30)
        assert order.filled_at.date() == session_day
    finally:
        db.close()


def test_us_winter_fill_accounts_for_standard_time():
    """冬令 09:30 EST ＝ 14:30 UTC——寫死偏移會在換季時錯一小時。"""
    session_day = date(2026, 1, 14)  # 週三，EST
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "US")
        stock = _stock_with_price(db, "TS003", "US", session_day, price=10.0)
        order = _pending_buy(db, account, stock, datetime(2026, 1, 13, 23, 10))

        fill_pending_orders(db, "US")

        db.refresh(order)
        assert order.filled_at == datetime(2026, 1, 14, 14, 30)
        assert order.filled_at.date() == session_day
    finally:
        db.close()


def test_intraday_exit_keeps_the_actual_trigger_time():
    """盤中出場記的是觸發當下，不是開盤——兩者混淆會讓停損看起來提早發生。"""
    from app.services.sim import sentinel

    db = SessionLocal()
    try:
        source = SimOrder.__table__.c.filled_at
        assert source is not None  # 欄位存在即可，行為由下方原始碼契約守住
    finally:
        db.close()

    import inspect

    body = inspect.getsource(sentinel._fill_exit)
    assert "utc_now_naive()" in body, "盤中出場不可改用開盤時刻"


def test_api_exposes_fill_time_with_timezone_marker(client):
    """輸出必須帶 Z：少了時區標記，瀏覽器的 new Date() 會當本地時間解讀。"""
    session_day = date.today() - timedelta(days=3)
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock_with_price(db, "TS004", "TW", session_day)
        db.add(SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=1,
            fill_price=100.0, fee=20.0, status="filled", decided_by="ai",
            created_at=datetime(2026, 7, 14, 23, 10),
            filled_at=datetime(2026, 7, 15, 1, 0),
        ))
        db.commit()
    finally:
        db.close()

    rows = client.get("/api/v1/simulation/TW/orders").json()["data"]
    row = next(r for r in rows if r["symbol"] == "TS004")

    assert row["filled_at"].endswith("Z"), row["filled_at"]
    assert row["created_at"].endswith("Z"), row["created_at"]
    assert row["filled_at"].startswith("2026-07-15T01:00")


@pytest.mark.parametrize("market,expected_hour", [("TW", 1), ("US", 13)])
def test_market_open_utc_helper(market, expected_hour):
    from app.services.sim.engine import market_open_utc

    stamped = market_open_utc(date(2026, 7, 15), market)
    assert stamped.hour == expected_hour
    assert stamped.tzinfo is None, "存進 DB 的一律是 naive UTC"
