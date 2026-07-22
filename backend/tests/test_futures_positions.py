"""台指期三大法人淨未平倉的解析：淨部位、日增減與缺資料處理。"""
from app.services.market_context import parse_futures_positions


def _row(day, name, long_, short):
    return {
        "date": day, "futures_id": "TX", "institutional_investors": name,
        "long_open_interest_balance_volume": long_,
        "short_open_interest_balance_volume": short,
    }


def test_computes_net_position_and_daily_change():
    rows = [
        _row("2026-07-21", "外資", 9_379, 87_869),
        _row("2026-07-21", "投信", 81_608, 6_038),
        _row("2026-07-21", "自營商", 5_607, 4_404),
        _row("2026-07-22", "外資", 8_455, 85_050),
        _row("2026-07-22", "投信", 81_796, 6_173),
        _row("2026-07-22", "自營商", 5_108, 3_969),
    ]
    result = {p["name"]: p for p in parse_futures_positions(rows)}

    assert result["外資"]["net"] == -76_595
    assert result["外資"]["change"] == 1_895  # -78,490 → -76,595，空單回補
    assert result["投信"]["net"] == 75_623
    assert result["自營商"]["net"] == 1_139


def test_change_is_none_without_previous_day():
    rows = [_row("2026-07-22", "外資", 8_455, 85_050)]
    result = parse_futures_positions(rows)

    assert result[0]["net"] == -76_595
    assert result[0]["change"] is None


def test_only_latest_day_is_reported():
    rows = [
        _row("2026-07-20", "外資", 1_000, 2_000),
        _row("2026-07-21", "外資", 1_000, 3_000),
        _row("2026-07-22", "外資", 1_000, 4_000),
    ]
    result = parse_futures_positions(rows)

    assert len(result) == 1
    assert result[0]["net"] == -3_000  # 只取 07-22
    assert result[0]["change"] == -1_000  # 與 07-21 相比


def test_returns_none_when_empty():
    assert parse_futures_positions([]) is None


def test_tolerates_missing_volume_fields():
    rows = [{"date": "2026-07-22", "institutional_investors": "外資"}]
    result = parse_futures_positions(rows)
    assert result[0]["net"] == 0
