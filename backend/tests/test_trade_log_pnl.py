"""交易日誌的金額與已實現損益。

日誌原本只顯示「股數 @ 成交價」，看不出這筆花了多少、賣掉賺賠多少。
金額與損益都由 filled orders 重放推導（與持倉同一套口徑），不另存欄位。

口徑：成本均價含買入手續費，賣出淨額扣賣出費用與稅——兩邊都算進去，
帳面損益才等於現金實際的增減。
"""
from datetime import date, datetime, timedelta

import pytest

from app.core.db import SessionLocal
from app.models import DailyPrice, SimOrder, Stock
from app.services.sim.engine import get_or_create_account
from app.services.sim.portfolio import realized_pnl_by_order


def _stock(db, symbol, market="TW"):
    stock = Stock(
        symbol=symbol, market=market, name=f"測試{symbol}", currency="TWD", kind="stock"
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _fill(db, account, stock, side, qty, price, fee, *, days_ago):
    """直接寫入一筆已成交訂單（撮合流程本身由 test_simulation 驗證）。"""
    order = SimOrder(
        account_id=account.id,
        stock_id=stock.id,
        side=side,
        qty=qty,
        fill_price=price,
        fee=fee,
        status="filled",
        decided_by="ai",
        created_at=datetime.now() - timedelta(days=days_ago + 1),
        filled_at=datetime.now() - timedelta(days=days_ago),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_sell_realized_pnl_counts_fees_on_both_sides():
    """買 100 股 @100（費 20）→ 賣 100 股 @110（費 50）。

    成本基礎 = 100*100 + 20 = 10,020（均價 100.2）
    賣出淨額 = 110*100 - 50 = 10,950
    已實現   = +930 → +9.28%
    """
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "PNL01")
        _fill(db, account, stock, "buy", 100, 100.0, 20.0, days_ago=10)
        sell = _fill(db, account, stock, "sell", 100, 110.0, 50.0, days_ago=5)

        pnl = realized_pnl_by_order(db, account)[sell.id]

        assert pnl["avg_cost"] == pytest.approx(100.2)
        assert pnl["realized_pnl"] == pytest.approx(930.0)
        assert pnl["realized_pnl_pct"] == pytest.approx(9.28, abs=0.01)
    finally:
        db.close()


def test_partial_sell_leaves_average_cost_untouched():
    """部分賣出按比例沖銷成本，剩餘部位的均價不變。

    均價若被賣出改動，之後每一筆的損益都會錯。
    """
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "PNL02")
        _fill(db, account, stock, "buy", 100, 100.0, 20.0, days_ago=10)
        first = _fill(db, account, stock, "sell", 40, 110.0, 20.0, days_ago=6)
        second = _fill(db, account, stock, "sell", 60, 110.0, 20.0, days_ago=4)

        by_order = realized_pnl_by_order(db, account)

        # 兩筆賣出看到的均價相同——第二筆不因第一筆而漂移
        assert by_order[first.id]["avg_cost"] == pytest.approx(100.2)
        assert by_order[second.id]["avg_cost"] == pytest.approx(100.2)
        # 40 股：4400 - 20 - 4008 = 372
        assert by_order[first.id]["realized_pnl"] == pytest.approx(372.0)
        # 60 股：6600 - 20 - 6012 = 568
        assert by_order[second.id]["realized_pnl"] == pytest.approx(568.0)
    finally:
        db.close()


def test_buy_orders_have_no_realized_pnl():
    """買進只是把現金換成部位，此刻沒有實現任何損益。"""
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "PNL03")
        buy = _fill(db, account, stock, "buy", 100, 100.0, 20.0, days_ago=10)

        assert buy.id not in realized_pnl_by_order(db, account)
    finally:
        db.close()


def test_losing_trade_is_reported_as_negative():
    """賣在成本之下要如實記為負，不可取絕對值。"""
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "PNL04")
        _fill(db, account, stock, "buy", 100, 100.0, 20.0, days_ago=10)
        sell = _fill(db, account, stock, "sell", 100, 90.0, 40.0, days_ago=5)

        pnl = realized_pnl_by_order(db, account)[sell.id]

        # 9000 - 40 - 10020 = -1060
        assert pnl["realized_pnl"] == pytest.approx(-1060.0)
        assert pnl["realized_pnl_pct"] < 0
    finally:
        db.close()


def test_orders_view_exposes_amounts_and_realized_pnl(client):
    """API 契約：日誌要能直接畫出「花了多少／收回多少／賺賠多少」。"""
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "PNL05")
        db.add(
            DailyPrice(
                stock_id=stock.id, date=date.today() - timedelta(days=1),
                open=100.0, high=101.0, low=99.0, close=100.0, volume=1000,
            )
        )
        db.commit()
        _fill(db, account, stock, "buy", 100, 100.0, 20.0, days_ago=10)
        _fill(db, account, stock, "sell", 100, 110.0, 50.0, days_ago=5)
    finally:
        db.close()

    rows = client.get("/api/v1/simulation/TW/orders").json()["data"]
    by_side = {r["side"]: r for r in rows if r["symbol"] == "PNL05"}

    buy = by_side["buy"]
    assert buy["gross_amount"] == pytest.approx(10000.0)
    # 買進的淨額是實際支出：成交金額 + 手續費
    assert buy["net_amount"] == pytest.approx(10020.0)
    assert buy["realized_pnl"] is None

    sell = by_side["sell"]
    assert sell["gross_amount"] == pytest.approx(11000.0)
    # 賣出的淨額是實際入袋：成交金額 - 費用與稅
    assert sell["net_amount"] == pytest.approx(10950.0)
    assert sell["avg_cost"] == pytest.approx(100.2)
    assert sell["realized_pnl"] == pytest.approx(930.0)
    assert sell["realized_pnl_pct"] == pytest.approx(9.28, abs=0.01)


def test_pending_order_has_no_amounts(client):
    """未成交沒有成交價，金額欄位必須是 null——填 0 會被讀成「這筆不用錢」。"""
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "PNL06")
        db.add(
            SimOrder(
                account_id=account.id, stock_id=stock.id, side="buy", qty=100,
                status="pending", decided_by="ai", created_at=datetime.now(),
            )
        )
        db.commit()
    finally:
        db.close()

    rows = client.get("/api/v1/simulation/TW/orders").json()["data"]
    pending = next(r for r in rows if r["symbol"] == "PNL06")

    assert pending["gross_amount"] is None
    assert pending["net_amount"] is None
