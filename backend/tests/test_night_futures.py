"""台指期夜盤解析的測試（build_market_context 的 TW 專屬段落）。

基準價取自夜盤合約自身的 CRefPrice（前一交易日結算價），不再另外查日盤。

原本拿「日盤 -F 合約的 CLastPrice」當基準，但簡報排在 06:55——台股日盤
09:00 才開盤，那時日盤根本還沒有成交價。正式環境因此連日印出
「日盤[TXFH6-F=空 …]」而夜盤區塊從缺（2026-08-17 06:55 的 log 即是如此）。
夜盤回應本身就帶著 CRefPrice/CDiff/CDiffRate，一次呼叫就夠。
"""
from app.services.market_context import parse_night_futures


def _q(symbol, last, ref="42000"):
    return {"SymbolID": symbol, "CLastPrice": last, "CRefPrice": ref}


def test_near_month_change_is_measured_against_reference_price():
    night = [
        _q("TXF-P", ""),  # 現貨無夜盤
        _q("TXFH6-M", "42758.00", ref="42725.00"),  # 近月（第一個有成交的合約）
        _q("TXFI6-M", "43061.00", ref="42975.00"),
    ]

    result = parse_night_futures(night)

    assert result is not None
    assert result["contract"] == "TXFH6"
    assert result["night_last"] == 42758
    assert result["day_close"] == 42725
    # (42758-42725)/42725 ≈ +0.08%
    assert result["change_pct"] == 0.08


def test_works_before_the_day_session_opens():
    """06:55 跑簡報時日盤尚未開盤——這正是原本失敗的情境。

    夜盤自帶基準價，故不受日盤有無成交影響。
    """
    night = [_q("TXFH6-M", "45727.00", ref="45812.00")]

    result = parse_night_futures(night)

    assert result is not None
    assert result["night_last"] == 45727
    assert result["day_close"] == 45812
    assert result["change_pct"] == -0.19  # 與期交所回傳的 CDiffRate 一致


def test_skips_contracts_without_a_traded_price():
    night = [_q("TXF-P", ""), _q("TXFH6-M", ""), _q("TXFI6-M", "43000", ref="42900")]

    result = parse_night_futures(night)

    assert result is not None
    assert result["contract"] == "TXFI6"


def test_handles_missing_or_unusable_data():
    assert parse_night_futures([]) is None
    assert parse_night_futures([_q("TXFH6-M", "abc")]) is None
    # 沒有參考價就算不出隔夜變化，寧可從缺也不要憑空生一個基準
    assert parse_night_futures([{"SymbolID": "TXFH6-M", "CLastPrice": "42000"}]) is None
    assert parse_night_futures([_q("TXFH6-M", "42000", ref="0")]) is None
