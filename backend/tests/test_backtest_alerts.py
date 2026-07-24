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
        _seed(db, "7301", [100 + i * 0.1 for i in range(600)])
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


def test_win_rate_uses_net_pnl_consistent_with_equity():
    """勝率必須與含費的 equity 曲線同一把尺。

    台股單筆來回約 0.585%（買 0.1425%＋賣 0.4425%）以內的正毛報酬
    其實是淨虧損；以毛報酬判定會把它算成勝場，勝率與權益表現對不上。
    """
    from app.services.backtest_service import _net_pnl_pct

    # 毛報酬 +0.3%（<0.585% 來回成本）→ 淨其實是虧的
    net = _net_pnl_pct("TW", 100.0, 100.3)
    assert net < 0, f"毛 +0.3% 在台股應為淨虧損，實得 {net}%"

    # 毛報酬 +2% 足以覆蓋成本 → 仍是勝場，但淨值低於毛值
    net_win = _net_pnl_pct("TW", 100.0, 102.0)
    assert 0 < net_win < 2.0

    # 美股零手續費 → 淨＝毛
    assert _net_pnl_pct("US", 100.0, 102.0) == pytest.approx(2.0, abs=0.01)


def test_backtest_win_rate_excludes_fee_only_wins():
    """毛正、淨負的交易不可算進勝率。

    直接餵人工 df/signals：進場 100、出場 100.4（毛 +0.4%），
    低於台股來回成本 0.585% → 淨必為負。用真實策略資料造不出這種
    交易（訊號慢一天會買高賣低，毛報酬本來就是負的），那樣的測試
    即使改回毛報酬公式也照樣綠，等於沒有守住任何東西。
    """
    df = pd.DataFrame({
        "date": [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
        "open": [100.0, 100.0, 100.4],
        "close": [100.0, 100.0, 100.4],
    })

    result = _simulate("TW", df, [1, 0, 0], "ma_cross")

    assert result["metrics"]["trades"] == 1
    trade = result["trades"][0]
    gross = (trade["exit_price"] / trade["entry_price"] - 1) * 100
    assert gross > 0, "測資本身必須是毛報酬為正，否則測不到重點"
    assert trade["pnl_pct"] < 0, f"毛 +{gross:.2f}% 扣費後應為負，實得 {trade['pnl_pct']}%"

    assert result["metrics"]["win_rate_pct"] is not None
    assert result["metrics"]["win_rate_pct"] == 0
    # 與含費的權益曲線同向
    assert result["metrics"]["total_return_pct"] < 0


def test_open_position_uses_same_yardstick_as_equity_curve():
    """未平倉部位＝逐日盯市，必須與 equity_curve 對得起來。

    若在此扣賣出費就成了第三種定義：既不等於 equity，也不等於
    已平倉交易的淨值。
    """
    df = pd.DataFrame({
        "date": [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
        "open": [100.0, 100.0, 105.0],
        "close": [100.0, 105.0, 110.0],
    })

    result = _simulate("TW", df, [1, 1, 1], "ma_cross")

    final_equity = result["equity_curve"][-1]["equity"]
    assert result["open_position"]["pnl_pct"] == pytest.approx(
        (final_equity - 1) * 100, abs=0.01
    )


def test_etf_pays_one_third_of_stock_transaction_tax():
    """台股 ETF 證交稅 0.1%，個股 0.3%——不分辨會讓 ETF 賣出成本高估近三倍。"""
    from app.services.backtest_service import _fee_rate
    from app.services.sim.engine import calc_fee

    # 回測：賣出費率
    stock_sell = _fee_rate("TW", "sell", is_etf=False)
    etf_sell = _fee_rate("TW", "sell", is_etf=True)
    assert stock_sell - etf_sell == pytest.approx(0.002, abs=1e-6)  # 0.3% - 0.1%
    assert _fee_rate("TW", "buy", is_etf=True) == _fee_rate("TW", "buy", is_etf=False)

    # 模擬引擎：同一筆金額，ETF 的稅少 0.2%
    gross = 1_000_000.0
    assert calc_fee("TW", "sell", gross, is_etf=False) - calc_fee(
        "TW", "sell", gross, is_etf=True
    ) == pytest.approx(gross * 0.002, abs=0.01)


def test_beats_buy_hold_compares_like_with_like():
    """基準也要含費，否則「淨 vs 毛」相減會系統性低估超額報酬。"""
    from app.services.backtest_service import _net_pnl_pct

    df = pd.DataFrame({
        "date": [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
        "open": [100.0, 100.0, 110.0],
        "close": [100.0, 105.0, 110.0],
    })

    result = _simulate("TW", df, [1, 1, 1], "ma_cross")
    reported = result["metrics"]["buy_hold_return_pct"]

    gross = (110.0 / 100.0 - 1) * 100  # 10.0%
    expected_net = _net_pnl_pct("TW", 100.0, 110.0)

    assert reported == pytest.approx(expected_net, abs=0.01), (
        f"buy_hold 應為含費淨值 {expected_net}%，實得 {reported}%"
    )
    assert reported < gross, "買入持有基準仍是毛報酬，與含費的策略報酬不同尺"
