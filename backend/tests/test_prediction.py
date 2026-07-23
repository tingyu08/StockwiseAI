from datetime import date, timedelta

import pytest

from app.core.db import SessionLocal
from app.models import DailyPrice, Stock
from app.services.prediction_service import _fit


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


def test_predictions_converge_on_concurrent_write(monkeypatch):
    """併發請求已寫入同一 (stock, date, method) → 兜底讀既有結果，不冒 IntegrityError。"""
    from sqlalchemy import func, select

    from app.models import Prediction
    from app.services import prediction_service as ps

    db = SessionLocal()
    try:
        stock = Stock(symbol="PREDC", market="TW", name="併發預測", currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        d = date.today() - timedelta(days=90)
        added = 0
        while added < 40:
            if d.weekday() < 5:
                db.add(DailyPrice(
                    stock_id=stock.id, date=d,
                    open=100, high=101, low=99, close=100 + added, volume=1000,
                ))
                added += 1
            d += timedelta(days=1)
        db.commit()
        sid = stock.id
        last_date = db.execute(
            select(DailyPrice.date)
            .where(DailyPrice.stock_id == sid)
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one()
    finally:
        db.close()

    real_fit = ps._fit
    injected = {"done": False}

    def racing_fit(values):
        # 模擬另一併發請求在本次 cache miss 之後、commit 之前已完成寫入
        if not injected["done"]:
            injected["done"] = True
            other = SessionLocal()
            try:
                other.add_all([
                    Prediction(
                        stock_id=sid, trade_date=last_date, horizon_days=h,
                        method=ps.METHOD, predicted_json="[]",
                    )
                    for h in ps.HORIZONS
                ])
                other.commit()
            finally:
                other.close()
        return real_fit(values)

    monkeypatch.setattr(ps, "_fit", racing_fit)

    db = SessionLocal()
    try:
        stock = db.get(Stock, sid)
        # 不應冒 IntegrityError；收斂回傳既有（併發者寫入的）結果
        result = ps.get_predictions(db, stock)
        assert injected["done"] is True
        assert set(result["horizons"].keys()) == {"5", "20"}
        count = db.execute(
            select(func.count()).select_from(Prediction).where(Prediction.stock_id == sid)
        ).scalar_one()
        assert count == len(ps.HORIZONS)  # 兜底後仍每 horizon 僅一筆，未寫成重複
    finally:
        db.close()
