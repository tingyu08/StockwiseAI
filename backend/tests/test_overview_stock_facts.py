"""簡報的標的名稱與昨收由系統直供，不靠 AI 複述。

stock_notes.yesterday 是 AI 複述我們餵給它的字串（見 _format_change）。
要拿它來上色就得反過來剖析中文，而 AI 隨時可能改寫格式；公司名稱它更是
完全沒有回傳，畫面上只剩一串代號。這些都是系統手上就有的確定資料。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock, WatchlistItem
from app.services.analysis_service import stock_facts

SYMBOL = "SFACT1"


@pytest.fixture(autouse=True)
def _clean_up():
    yield
    db = SessionLocal()
    try:
        ids = [
            row.id
            for row in db.execute(
                select(Stock).where(Stock.symbol.like("SFACT%"))
            ).scalars()
        ]
        if ids:
            db.execute(delete(WatchlistItem).where(WatchlistItem.stock_id.in_(ids)))
            db.execute(delete(DailyPrice).where(DailyPrice.stock_id.in_(ids)))
            db.execute(delete(Stock).where(Stock.id.in_(ids)))
            db.commit()
    finally:
        db.close()


def _seed(db, symbol, name, closes):
    stock = Stock(
        symbol=symbol, market="TW", name=name, currency="TWD", kind="etf"
    )
    db.add(stock)
    db.commit()
    db.refresh(stock)
    day = date.today() - timedelta(days=len(closes))
    for close in closes:  # 由舊到新
        db.add(DailyPrice(
            stock_id=stock.id, date=day, open=close, high=close,
            low=close, close=close, volume=1000,
        ))
        day += timedelta(days=1)
    db.add(WatchlistItem(stock_id=stock.id, ai_managed=True))
    db.commit()
    return stock


def test_facts_carry_the_company_name():
    """畫面要顯示名稱，而 AI 的 stock_notes 根本沒有這個欄位。"""
    db = SessionLocal()
    try:
        _seed(db, SYMBOL, "主動統一升級50", [10.1, 10.3])
        facts = stock_facts(db, "TW")
    finally:
        db.close()

    assert facts[SYMBOL]["name"] == "主動統一升級50"


def test_change_pct_is_signed_for_colouring():
    """漲跌以帶正負號的數值提供，前端才不必去剖析中文字串來決定顏色。"""
    db = SessionLocal()
    try:
        _seed(db, SYMBOL, "上漲標的", [10.0, 10.3])   # +3%
        _seed(db, "SFACT2", "下跌標的", [10.0, 9.5])   # -5%
        _seed(db, "SFACT3", "平盤標的", [10.0, 10.0])  # 0%
        facts = stock_facts(db, "TW")
    finally:
        db.close()

    assert facts[SYMBOL]["close"] == 10.3
    assert facts[SYMBOL]["change_pct"] == pytest.approx(3.0)
    assert facts["SFACT2"]["change_pct"] == pytest.approx(-5.0)
    assert facts["SFACT3"]["change_pct"] == pytest.approx(0.0)


def test_single_day_history_yields_null_change_not_zero():
    """只有一天資料時漲跌未知——填 0 會被畫成平盤，等於謊報。"""
    db = SessionLocal()
    try:
        _seed(db, SYMBOL, "新上市", [10.0])
        facts = stock_facts(db, "TW")
    finally:
        db.close()

    assert facts[SYMBOL]["close"] == 10.0
    assert facts[SYMBOL]["change_pct"] is None


def test_dto_omits_facts_when_no_session_given():
    """工作中心的 job result 不帶行情快照：省一次查詢，也避免把當下的
    價格凍結在歷史工作紀錄裡。"""
    from types import SimpleNamespace

    from app.services.analysis_service import overview_dto

    overview = SimpleNamespace(
        market="TW", trade_date=date(2026, 8, 14), model="test",
        payload_json="{}", created_at=None,
    )

    assert "stock_facts" not in overview_dto(overview)


def test_api_response_includes_facts(client):
    """契約：前端排版與上色所需的資料要出現在 overview 回應裡。"""
    db = SessionLocal()
    try:
        _seed(db, SYMBOL, "主動統一升級50", [10.1, 10.3])
    finally:
        db.close()

    res = client.get("/api/v1/analysis/TW/overview")
    if res.status_code == 404:
        pytest.skip("尚無當日簡報，契約由上面的單元測試涵蓋")
    facts = res.json()["data"]["stock_facts"]
    assert facts[SYMBOL]["name"] == "主動統一升級50"
