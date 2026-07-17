"""台指期夜盤解析的測試（build_market_context 的 TW 專屬段落）。"""
from app.services.market_context import parse_night_futures


def _q(symbol, last, ref="42000"):
    return {"SymbolID": symbol, "CLastPrice": last, "CRefPrice": ref}


def test_parse_night_futures_near_month_change():
    night = [
        _q("TXF-P", ""),  # 現貨無夜盤
        _q("TXFH6-M", "42758.00"),  # 近月（第一個有成交的合約）
        _q("TXFI6-M", "43061.00"),
    ]
    day = [
        _q("TXF-S", "42671.27"),
        _q("TXFH6-F", "42725.00"),
        _q("TXFI6-F", "42975.00"),
    ]
    result = parse_night_futures(night, day)
    assert result is not None
    assert result["contract"] == "TXFH6"
    assert result["night_last"] == 42758
    assert result["day_close"] == 42725
    # (42758-42725)/42725 ≈ +0.08%
    assert result["change_pct"] == 0.08


def test_parse_night_futures_skips_untradeed_contracts():
    night = [_q("TXF-P", ""), _q("TXFH6-M", ""), _q("TXFI6-M", "43000")]
    day = [_q("TXFH6-F", ""), _q("TXFI6-F", "42900")]
    result = parse_night_futures(night, day)
    assert result is not None
    assert result["contract"] == "TXFI6"


def test_parse_night_futures_handles_missing_data():
    assert parse_night_futures([], []) is None
    assert parse_night_futures([_q("TXFH6-M", "abc")], [_q("TXFH6-F", "42000")]) is None
    assert parse_night_futures([_q("TXFH6-M", "42000")], [_q("TXFH6-F", "0")]) is None
