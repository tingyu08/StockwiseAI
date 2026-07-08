from datetime import date, timedelta

import pytest

from app.core.db import SessionLocal
from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Stock
from app.services.compare_service import _annualized, _trailing_return, _volatility, compare


def test_trailing_return_golden():
    values = [100.0] * 20 + [110.0]
    assert _trailing_return(values, 5) == pytest.approx(10.0)


def test_trailing_return_insufficient_data():
    assert _trailing_return([100.0, 101.0], 5) is None


def test_annualized_one_year_doubling():
    # 253 個點（252 個交易日間隔）翻倍 → 年化 100%
    values = [100 * (2 ** (i / 252)) for i in range(253)]
    assert _annualized(values) == pytest.approx(100.0, abs=0.5)


def test_volatility_constant_prices_is_zero():
    assert _volatility([100.0] * 30) == pytest.approx(0.0)


def test_compare_endpoint(client):
    db = SessionLocal()
    try:
        for sym, base in [("AAA", 100.0), ("BBB", 50.0)]:
            s = Stock(symbol=sym, market="TW", name=sym, currency="TWD", kind="stock")
            db.add(s)
            db.commit()
            db.refresh(s)
            for i in range(40):
                db.add(DailyPrice(
                    stock_id=s.id, date=date.today() - timedelta(days=40 - i),
                    open=base, high=base, low=base, close=base * (1 + i * 0.01), volume=100,
                ))
            db.commit()
    finally:
        db.close()

    res = client.get("/api/v1/compare", params={"market": "TW", "symbols": "AAA,BBB", "range": "3m"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data["metrics"]) == 2
    # 正規化：兩檔首日皆為 100
    assert data["series"]["AAA"][0]["value"] == 100.0
    assert data["series"]["BBB"][0]["value"] == 100.0
    # 同樣的成長軌跡 → 正規化終值相同
    assert data["series"]["AAA"][-1]["value"] == data["series"]["BBB"][-1]["value"]


def test_compare_untracked_symbol_404(client):
    res = client.get("/api/v1/compare", params={"market": "TW", "symbols": "ZZZZ", "range": "3m"})
    assert res.status_code == 404


def test_compare_too_many_symbols_400(client):
    syms = ",".join(f"S{i}" for i in range(9))
    res = client.get("/api/v1/compare", params={"market": "TW", "symbols": syms, "range": "3m"})
    assert res.status_code == 400
