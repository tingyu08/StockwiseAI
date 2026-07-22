from datetime import date, timedelta
import inspect

import pytest
import pandas as pd
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import DailyPrice, EtfNav, Stock
from app.models.alert import Alert, AlertEvent
from app.services.alert_service import check_alerts
from app.services import backtest_service
from app.services.backtest_service import _max_drawdown, _simulate, run_backtest


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
        assert m["trades"] + (1 if result["open_position"] else 0) >= 1
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


def test_backtest_open_position_is_not_counted_as_closed_trade():
    df = pd.DataFrame({
        "date": [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
        "open": [100.0, 100.0, 105.0],
        "close": [100.0, 105.0, 110.0],
    })

    result = _simulate("US", df, [1, 1, 1], "ma_cross")

    assert result["metrics"]["trades"] == 0
    assert result["metrics"]["win_rate_pct"] is None
    assert result["open_position"]["entry_price"] == 100.0


def test_backtest_slippage_reduces_strategy_return():
    df = pd.DataFrame({
        "date": [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
        "open": [100.0, 100.0, 110.0],
        "close": [100.0, 105.0, 110.0],
    })

    if "slippage_bps" not in inspect.signature(_simulate).parameters:
        pytest.fail("_simulate must accept slippage_bps")
    without_slippage = _simulate("US", df, [1, 0, 0], "ma_cross", slippage_bps=0)
    with_slippage = _simulate("US", df, [1, 0, 0], "ma_cross", slippage_bps=100)

    assert with_slippage["metrics"]["total_return_pct"] < without_slippage["metrics"]["total_return_pct"]


def test_sharpe_ratio_is_annualized_and_handles_flat_curve():
    sharpe = getattr(backtest_service, "_sharpe_ratio", None)
    assert callable(sharpe)
    if not sharpe:
        return
    assert sharpe([1.0, 1.0, 1.0]) is None
    assert sharpe([1.0, 1.01, 1.03, 1.06]) > 0


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
        assert r1["events"][0]["symbol"] == "7003"
        assert r1["events"][0]["value"] == 150.0
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


def test_backtest_window_excludes_indicator_warmup():
    """暖身用的 120 天不得混進績效區間。

    多抓 120 天是為了讓 MA60/RSI 在視窗起點就有效；但若不切回
    range_days，annualized/sharpe/buy_hold/period 都會落在比使用者
    要求更長的區間，且前段未持倉的平盤日會系統性拉低數字。
    """
    db = SessionLocal()
    try:
        # 600 個交易日 ≈ 涵蓋 range_days + 120 天緩衝仍有餘裕
        stock = _seed(db, "7301", [100 + i * 0.1 for i in range(600)])
        result = run_backtest(db, "TW", "7301", "ma_cross", range_days=365)

        window_start = date.today() - timedelta(days=365)
        period_start = date.fromisoformat(result["period"]["start"])
        assert period_start >= window_start, (
            f"回測起點 {period_start} 早於要求的 {window_start}（暖身資料混入績效）"
        )

        # 權益曲線也不得含視窗外的日期
        first_curve_date = date.fromisoformat(result["equity_curve"][0]["date"])
        assert first_curve_date >= window_start

        # 365 天視窗約 250 個交易日；若含 120 天緩衝會膨脹到 330+
        assert len(result["equity_curve"]) < 300, (
            f"權益曲線 {len(result['equity_curve'])} 筆，疑似仍含暖身區間"
        )
    finally:
        db.close()
