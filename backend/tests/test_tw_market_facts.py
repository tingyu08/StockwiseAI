"""台股籌碼面／基本面摘要：分項、連續天數、單位換算與缺資料的處理。"""
from app.services import tw_market_facts as facts


def _flow(day, name, buy, sell):
    return {"date": day, "stock_id": "2330", "name": name, "buy": buy, "sell": sell}


def test_flow_summary_splits_three_investor_groups():
    rows = [
        # 外資賣超（含外資自營）、投信買超、自營商買超
        _flow("2026-07-21", "Foreign_Investor", 1_000_000, 3_000_000),
        _flow("2026-07-21", "Foreign_Dealer_Self", 0, 0),
        _flow("2026-07-21", "Investment_Trust", 2_000_000, 0),
        _flow("2026-07-21", "Dealer_self", 500_000, 100_000),
        _flow("2026-07-21", "Dealer_Hedging", 300_000, 200_000),
    ]
    summary = facts.build_flow_summary(rows)

    assert "外資賣超 2,000 張" in summary
    assert "投信買超 2,000 張" in summary
    assert "自營商買超 500 張" in summary  # 自行 400 ＋ 避險 100
    assert "合計" not in summary  # 不再只給加總


def test_flow_summary_reports_consecutive_days():
    rows = []
    for day in ("2026-07-17", "2026-07-20", "2026-07-21"):
        rows.append(_flow(day, "Foreign_Investor", 0, 1_000_000))  # 連 3 日賣超
        rows.append(_flow(day, "Investment_Trust", 1_000_000, 0))  # 連 3 日買超
    summary = facts.build_flow_summary(rows)

    assert "外資賣超 3,000 張（連3日賣超）" in summary
    assert "投信買超 3,000 張（連3日買超）" in summary


def test_flow_streak_breaks_on_direction_change():
    rows = [
        _flow("2026-07-17", "Foreign_Investor", 5_000_000, 0),
        _flow("2026-07-20", "Foreign_Investor", 0, 1_000_000),
        _flow("2026-07-21", "Foreign_Investor", 0, 1_000_000),
    ]
    # 期間淨買超 3,000 張，但最近兩日轉賣超 → 用「轉」避免語意矛盾
    summary = facts.build_flow_summary(rows)
    assert "外資買超 3,000 張（最近2日轉賣超）" in summary


def test_flow_streak_uses_lian_when_direction_matches_period():
    rows = [
        _flow("2026-07-20", "Foreign_Investor", 0, 1_000_000),
        _flow("2026-07-21", "Foreign_Investor", 0, 1_000_000),
    ]
    assert "外資賣超 2,000 張（連2日賣超）" in facts.build_flow_summary(rows)


def test_flow_summary_empty_when_no_rows():
    assert facts.build_flow_summary([]) == ""


def test_margin_summary_reports_change_and_short_ratio():
    rows = [
        {"date": "2026-07-17", "MarginPurchaseTodayBalance": 40_000,
         "ShortSaleTodayBalance": 100},
        {"date": "2026-07-21", "MarginPurchaseTodayBalance": 32_000,
         "ShortSaleTodayBalance": 96},
    ]
    summary = facts.build_margin_summary(rows)

    assert "融資餘額 32,000 張" in summary
    assert "-20.0%" in summary  # 40,000 → 32,000
    assert "融券餘額 96 張" in summary
    assert "券資比 0.30%" in summary


def test_valuation_summary_includes_range():
    rows = [
        {"date": "2026-07-17", "PER": 28.1, "PBR": 9.5, "dividend_yield": 1.05},
        {"date": "2026-07-21", "PER": 32.4, "PBR": 10.61, "dividend_yield": 0.91},
    ]
    summary = facts.build_valuation_summary(rows)

    assert "本益比 32.4" in summary
    assert "28.1~32.4" in summary
    assert "股價淨值比 10.61" in summary
    assert "現金殖利率 0.91%" in summary


def test_revenue_summary_computes_mom_and_yoy():
    rows = [
        {"revenue_year": 2025, "revenue_month": 6, "revenue": 300_000_000_000},
        {"revenue_year": 2026, "revenue_month": 5, "revenue": 416_975_163_000},
        {"revenue_year": 2026, "revenue_month": 6, "revenue": 442_679_969_000},
    ]
    summary = facts.build_revenue_summary(rows)

    assert "2026/06" in summary
    assert "4,427 億元" in summary
    assert "月增 +6.2%" in summary
    assert "年增 +47.6%" in summary


def test_revenue_summary_without_year_ago_omits_yoy():
    rows = [
        {"revenue_year": 2026, "revenue_month": 5, "revenue": 100},
        {"revenue_year": 2026, "revenue_month": 6, "revenue": 110},
    ]
    summary = facts.build_revenue_summary(rows)

    assert "月增 +10.0%" in summary
    assert "年增" not in summary


def test_summaries_tolerate_missing_fields():
    assert facts.build_margin_summary([]) == ""
    assert facts.build_valuation_summary([]) == ""
    assert facts.build_revenue_summary([]) == ""
    assert facts.build_revenue_summary([{"revenue_year": 2026, "revenue_month": 6,
                                         "revenue": 0}]) == ""
