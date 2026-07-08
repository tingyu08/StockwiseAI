from datetime import date, timedelta

import pytest

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock
from app.services.prediction_service import _fit, get_predictions


def test_fit_perfect_line():
    slope, intercept, sigma = _fit([10.0 + 2 * i for i in range(30)])
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(10.0)
    assert sigma == pytest.approx(0.0, abs=1e-9)


def test_fit_flat_series():
    slope, _, sigma = _fit([50.0] * 40)
    assert slope == pytest.approx(0.0)
    assert sigma == pytest.approx(0.0, abs=1e-9)


def test_predictions_shape_and_cache(client):
    db = SessionLocal()
    try:
        stock = Stock(symbol="PRED", market="TW", name="預測測試", currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        d = date.today() - timedelta(days=90)
        added = 0
        while added < 60:
            if d.weekday() < 5:
                db.add(DailyPrice(
                    stock_id=stock.id, date=d,
                    open=100, high=101, low=99, close=100 + added * 0.5, volume=1000,
                ))
                added += 1
            d += timedelta(days=1)
        db.commit()
        sid = stock.id
    finally:
        db.close()

    res = client.get("/api/v1/stocks/PRED/predictions", params={"market": "TW"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert set(data["horizons"].keys()) == {"5", "20"}
    band5 = data["horizons"]["5"]
    assert len(band5) == 5
    for point in band5:
        assert point["lower"] <= point["mid"] <= point["upper"]
    # 上升趨勢 → 投影中線高於最後收盤
    assert band5[-1]["mid"] > 100

    # 第二次呼叫走快取（predictions 表已有當日紀錄）
    res2 = client.get("/api/v1/stocks/PRED/predictions", params={"market": "TW"})
    assert res2.json()["data"] == data

    db = SessionLocal()
    try:
        from app.models import Prediction
        from sqlalchemy import select, func
        count = db.execute(
            select(func.count()).select_from(Prediction).where(Prediction.stock_id == sid)
        ).scalar_one()
        assert count == 2  # 5 日與 20 日各一筆，未重複寫入
    finally:
        db.close()
