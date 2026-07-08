import pandas as pd
import pytest

from app.services.indicator_service import compute_indicators


def make_prices(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=n, freq="D").date,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        }
    )


def test_empty_input_returns_empty_frame():
    out = compute_indicators(pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]))
    assert out.empty


def test_ma_golden_values():
    out = compute_indicators(make_prices([10, 11, 12, 13, 14, 15]))
    # MA5 於第 5 筆 = (10+11+12+13+14)/5 = 12
    assert out["ma5"].iloc[4] == pytest.approx(12.0)
    assert out["ma5"].iloc[5] == pytest.approx(13.0)
    assert pd.isna(out["ma5"].iloc[3])  # 資料不足為 NaN


def test_rsi_all_gains_approaches_100():
    out = compute_indicators(make_prices(list(range(100, 130))))
    assert out["rsi14"].iloc[-1] > 99


def test_rsi_all_losses_approaches_0():
    out = compute_indicators(make_prices(list(range(130, 100, -1))))
    assert out["rsi14"].iloc[-1] < 1


def test_bollinger_bands_symmetric_around_ma20():
    closes = [100 + (i % 5) for i in range(40)]
    out = compute_indicators(make_prices(closes))
    last = out.iloc[-1]
    mid = (last["bb_upper"] + last["bb_lower"]) / 2
    assert mid == pytest.approx(last["ma20"], rel=1e-9)


def test_kd_within_0_100():
    closes = [100 + (i % 7) * 2 for i in range(30)]
    out = compute_indicators(make_prices(closes))
    valid = out.dropna(subset=["kd_k", "kd_d"])
    assert ((valid["kd_k"] >= 0) & (valid["kd_k"] <= 100)).all()
    assert ((valid["kd_d"] >= 0) & (valid["kd_d"] <= 100)).all()


def test_input_not_mutated():
    prices = make_prices([10, 11, 12])
    original = prices.copy(deep=True)
    compute_indicators(prices)
    pd.testing.assert_frame_equal(prices, original)
