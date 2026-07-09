from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import DailyPrice, EtfNav, Stock
from app.models.alert import Alert, AlertEvent
from app.services.alert_service import check_alerts
from app.services.backtest_service import _max_drawdown, run_backtest


def _seed(db, symbol, closes, market="TW", kind="stock"):
    stock = Stock(symbol=symbol, market=market, name=f"測試{symbol}", currency="TWD", kind=kind)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    d = date.today() - timedelta(days=int(len(closes) * 1.6) + 10)
    added = 0
    while added < len(closes):
        if d.weekday() < 5:
            c = closes[added]
            db.add(DailyPrice(stock_id=stock.id, date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000))
            added += 1
        d += timedelta(days=1)
    db.commit()
    return stock


# ---- 回測 ----

def test_max_drawdown_golden():
    assert _max_drawdown([1.0, 1.2, 0.9, 1.1]) == pytest.approx(0.25)
    assert _max_drawdown([1.0, 1.1, 1.2]) == 0.0


def test_backtest_ma_cross_uptrend(client):
    db = SessionLocal()
    try:
        # 前段盤整＋後段強漲：MA 交叉策略應有至少一筆獲利交易
        closes = [100 + (i % 3) for i in range(60)] + [100 + i * 2 for i in range(60)]
        _seed(db, "7001", closes)
        result = run_backtest(db, "TW", "7001", "ma_cross", range_days=400)
        m = result["metrics"]
        assert m["trades"] + (1 if result["trades"] and result["trades"][-1]["exit_date"] is None else 0) >= 1
        assert m["total_return_pct"] > 0
        assert 0 <= m["max_drawdown_pct"] <= 100
        assert len(result["equity_curve"]) == 120
        assert result["equity_curve"][0]["equity"] == pytest.approx(1.0)
    finally:
        db.close()


def test_backtest_unknown_strategy_404(client):
    res = client.post(
        "/api/v1/backtest",
        json={"market": "TW", "symbol": "7001", "strategy": "yolo", "range_days": 365},
    )
    assert res.status_code == 422  # Literal 驗證擋下


def test_backtest_insufficient_data(client):
    db = SessionLocal()
    try:
        _seed(db, "7002", [100.0] * 30)
        res = client.post(
            "/api/v1/backtest",
            json={"market": "TW", "symbol": "7002", "strategy": "ma_cross", "range_days": 365},
        )
        assert res.status_code == 404
    finally:
        db.close()


# ---- 警示 ----

def test_price_alert_triggers_once_per_day(client):
    db = SessionLocal()
    try:
        stock = _seed(db, "7003", [100.0] * 30 + [150.0])
        alert = Alert(stock_id=stock.id, kind="price_above", threshold=120)
        db.add(alert)
        db.commit()

        r1 = check_alerts(db, "TW")
        assert r1["triggered"] == 1
        r2 = check_alerts(db, "TW")  # 同日重複檢查不重複觸發
        assert r2["triggered"] == 0

        event = db.execute(select(AlertEvent).where(AlertEvent.alert_id == alert.id)).scalar_one()
        assert float(event.value) == 150.0
    finally:
        db.close()


def test_price_alert_not_triggered_below_threshold(client):
    db = SessionLocal()
    try:
        stock = _seed(db, "7004", [100.0] * 30)
        db.add(Alert(stock_id=stock.id, kind="price_above", threshold=120))
        db.commit()
        before = db.execute(select(AlertEvent)).scalars().all()
        check_alerts(db, "TW")
        after = db.execute(select(AlertEvent)).scalars().all()
        assert len(after) == len(before)
    finally:
        db.close()


def test_premium_alert(client):
    db = SessionLocal()
    try:
        stock = _seed(db, "7005", [20.0] * 30, kind="etf")
        db.add(EtfNav(stock_id=stock.id, date=date.today(), nav=20.0, close=19.0, premium_pct=-5.0))
        db.add(Alert(stock_id=stock.id, kind="premium_below", threshold=-3.0))
        db.commit()
        result = check_alerts(db, "TW")
        assert result["triggered"] == 1
    finally:
        db.close()


def test_premium_alert_on_non_etf_rejected(client):
    res = client.post(
        "/api/v1/alerts",
        json={"market": "TW", "symbol": "7003", "kind": "premium_below", "threshold": -3},
    )
    assert res.status_code == 404
