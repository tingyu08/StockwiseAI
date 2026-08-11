"""撮合中斷留下的 'filling' 訂單必須被回收。

正式環境實例：兩筆 00403A（19,056 股）從 2026-07-09、07-10 卡在 filling
整整一個月。成因是狀態機有死路——撿訂單的查詢全都只看 status == 'pending'
（engine / decision / sentinel 三處皆然），沒有任何程式碼會再碰 filling。
還原 pending 撞唯一索引時，原本只記一行「維持 filling 待下輪」，
但根本沒有下輪。

回收採「過期即拒絕」而非「還原成 pending」：那些決策是一個月前做的，
拿舊判斷去吃今天的開盤價比不成交更糟。

不可無條件回收所有 filling：手動撮合（POST :fill）是同步直呼，不經 job
queue，可能與排程撮合並行——正在被另一個流程處理的訂單不能被搶走。
故以 filling_since 當租約，只回收明顯逾時者。
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import DailyPrice, SimOrder, Stock
from app.services.sim.engine import (
    STALE_FILLING_SECONDS,
    fill_pending_orders,
    get_or_create_account,
)
from app.services.time_service import utc_now_naive

SYMBOL_PREFIX = "SF0"


@pytest.fixture(autouse=True)
def _clean_up():
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


def _stock(db, symbol, market="TW", session_day=None, price=100.0):
    stock = Stock(
        symbol=symbol, market=market, name=f"測試{symbol}",
        currency="TWD", kind="stock",
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    if session_day is not None:
        db.add(DailyPrice(
            stock_id=stock.id, date=session_day,
            open=price, high=price, low=price, close=price, volume=1000,
        ))
        db.commit()
    return stock


def _filling_order(db, account, stock, *, filling_since):
    order = SimOrder(
        account_id=account.id, stock_id=stock.id, side="buy", qty=100,
        status="filling", decided_by="ai",
        created_at=datetime(2026, 7, 9, 8, 17),
        filling_since=filling_since,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_lease_is_long_enough_to_never_hit_a_live_fill():
    """撮合單筆是秒級的；租約要遠大於它，才不會搶走進行中的訂單。"""
    assert STALE_FILLING_SECONDS >= 600


def test_expired_filling_order_is_rejected_not_resurrected():
    """逾時的 filling 標成 rejected——決策已過期，不可拿去吃今天的開盤價。"""
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "SF001")
        stale = _filling_order(
            db, account, stock,
            filling_since=utc_now_naive() - timedelta(days=30),
        )

        fill_pending_orders(db, "TW")

        db.refresh(stale)
        assert stale.status == "rejected"
        assert stale.reject_reason, "拒絕必須說明原因"
        assert "撮合" in stale.reject_reason or "中斷" in stale.reject_reason
    finally:
        db.close()


def test_a_fill_in_progress_is_never_stolen():
    """剛進入 filling 的訂單屬於另一個進行中的撮合，不可回收。

    手動撮合不經 job queue，與排程並行是可能的。
    """
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "SF002")
        fresh = _filling_order(db, account, stock, filling_since=utc_now_naive())

        fill_pending_orders(db, "TW")

        db.refresh(fresh)
        assert fresh.status == "filling", "進行中的撮合被搶走了"
    finally:
        db.close()


def test_filling_without_a_lease_timestamp_is_still_recoverable():
    """本次改動之前留下的 filling 沒有 filling_since，一樣要收得掉。

    migration 會清掉既有的，但防禦性程式碼不該假設資料一定乾淨。
    """
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "SF003")
        orphan = _filling_order(db, account, stock, filling_since=None)

        fill_pending_orders(db, "TW")

        db.refresh(orphan)
        assert orphan.status == "rejected"
    finally:
        db.close()


def test_restoring_to_pending_falls_back_to_reject_on_conflict():
    """還原撞唯一索引時要 reject，不可留在 filling（那就是死路）。

    情境：同一 (account, stock) 已有另一筆 pending（哨兵新建的），
    此時把 filling 改回 pending 會違反 partial unique index。
    """
    from app.services.sim.engine import restore_or_reject

    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "SF004")
        blocking = SimOrder(
            account_id=account.id, stock_id=stock.id, side="sell", qty=1,
            status="pending", decided_by="ai", created_at=utc_now_naive(),
        )
        db.add(blocking)
        db.commit()
        stuck = _filling_order(db, account, stock, filling_since=utc_now_naive())

        outcome = restore_or_reject(db, stuck)
        db.commit()

        db.refresh(stuck)
        assert outcome == "rejected"
        assert stuck.status == "rejected"
        # 擋路的那筆不可被波及
        db.refresh(blocking)
        assert blocking.status == "pending"
    finally:
        db.close()


def test_normal_restore_still_returns_the_order_to_pending():
    """沒有衝突時仍要還原成 pending——等下一個交易日的開盤價，這是正常路徑。"""
    from app.services.sim.engine import restore_or_reject

    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        stock = _stock(db, "SF005")
        waiting = _filling_order(db, account, stock, filling_since=utc_now_naive())

        outcome = restore_or_reject(db, waiting)
        db.commit()

        db.refresh(waiting)
        assert outcome == "pending"
        assert waiting.status == "pending"
        assert waiting.filling_since is None, "還原後租約要清掉"
    finally:
        db.close()


def test_successful_fill_clears_the_lease():
    """成交後 filling_since 要清乾淨，不留誤導性的租約時間。"""
    session_day = date(2026, 7, 15)
    db = SessionLocal()
    try:
        account = get_or_create_account(db, "TW")
        original_cash = float(account.cash)
        stock = _stock(db, "SF006", session_day=session_day, price=100.0)
        account.cash = 100_000.0
        order = SimOrder(
            account_id=account.id, stock_id=stock.id, side="buy", qty=150,
            status="pending", decided_by="ai",
            created_at=datetime(2026, 7, 14, 23, 10),
        )
        db.add(order)
        db.commit()

        fill_pending_orders(db, "TW")

        db.refresh(order)
        assert order.status == "filled"
        assert order.filling_since is None
    finally:
        account = get_or_create_account(db, "TW")
        account.cash = original_cash
        db.commit()
        db.close()
