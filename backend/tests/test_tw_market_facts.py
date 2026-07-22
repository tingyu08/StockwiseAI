"""台股籌碼面／基本面摘要：分項、連續天數、單位換算與缺資料的處理。"""
from datetime import date

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
    assert facts.build_income_summary([]) == ""
    assert facts.build_balance_summary([]) == ""
    assert facts.build_cashflow_summary([], []) == ""
    assert facts.build_short_sale_summary([]) == ""


# ---- 借券與融券管制 ----

def test_short_sale_summary_reports_balance_and_change():
    rows = [
        {"date": "2026-07-17", "SBLShortSalesCurrentDayBalance": 10_000_000},
        {"date": "2026-07-21", "SBLShortSalesCurrentDayBalance": 12_000_000},
    ]
    summary = facts.build_short_sale_summary(rows)
    assert "借券賣出餘額 12,000 張" in summary
    assert "+20.0%" in summary


def test_suspension_only_reported_while_still_active():
    today = date(2026, 7, 22)
    expired = [{"date": "2026-06-05", "end_date": "2026-06-10", "reason": "除息"}]
    active = [{"date": "2026-07-20", "end_date": "2026-07-25", "reason": "除息"}]

    assert facts.build_suspension_summary(expired, today) == ""
    assert "2026-07-20~2026-07-25（除息）" in facts.build_suspension_summary(active, today)


# ---- 財報三表 ----

def _long(day, **fields):
    return [{"date": day, "type": k, "value": v} for k, v in fields.items()]


def test_income_summary_computes_margins_and_eps_change():
    rows = (
        _long("2025-12-31", Revenue=900e9, GrossProfit=500e9, OperatingIncome=400e9,
              IncomeAfterTaxes=350e9, EPS=13.5)
        + _long("2026-03-31", Revenue=965.6e9, GrossProfit=562e9, OperatingIncome=464e9,
                IncomeAfterTaxes=407e9, EPS=15.2)
    )
    summary = facts.build_income_summary(rows)

    assert "財報 2026-03-31" in summary
    assert "營收 9,656 億元" in summary
    assert "毛利率 58.2%" in summary
    assert "營益率 48.1%" in summary
    assert "淨利率 42.1%" in summary
    assert "EPS 15.20 元（前季 13.50）" in summary


def test_balance_summary_reports_debt_and_liquidity():
    rows = _long("2026-03-31", TotalAssets=8660.9e9, Liabilities=2728.6e9,
                 CurrentAssets=4265.5e9, CurrentLiabilities=1714.3e9,
                 CashAndCashEquivalents=3035.6e9)
    summary = facts.build_balance_summary(rows)

    assert "負債比 31.5%" in summary
    assert "流動比 2.49" in summary
    assert "現金 30,356 億元" in summary


def test_cashflow_summary_compares_against_net_income():
    cash = _long("2026-03-31", NetCashInflowFromOperatingActivities=580e9)
    income = _long("2026-03-31", IncomeAfterTaxes=407e9)
    summary = facts.build_cashflow_summary(cash, income)

    assert "營運現金流 5,800 億元" in summary
    assert "為稅後淨利 1.43 倍" in summary


def test_income_summary_skips_when_revenue_missing():
    assert facts.build_income_summary(_long("2026-03-31", EPS=15.2)) == ""


# ---- 快取：同一交易日同一檔只實際抓取一次 ----

async def test_build_tw_facts_caches_per_symbol_and_day(monkeypatch):
    calls = {"n": 0}

    class _Provider:
        def __getattr__(self, name):
            async def fetch(*args, **kwargs):
                calls["n"] += 1
                return []
            return fetch

    facts._CACHE.clear()
    facts._ACTIVE_ETF_CACHE.clear()
    monkeypatch.setattr(facts, "FinMindProvider", _Provider)
    monkeypatch.setattr(facts, "market_today", lambda market: date(2026, 7, 22))

    first = await facts.build_tw_facts("2330")
    after_first = calls["n"]
    second = await facts.build_tw_facts("2330")

    assert after_first == 9  # 個股：法人/融資券/借券/暫停融券/估值/月營收/三張財報
    assert calls["n"] == after_first, "第二次應完全命中快取，不得再打 API"
    assert second == first


async def test_build_tw_facts_evicts_previous_days(monkeypatch):
    class _Provider:
        def __getattr__(self, name):
            async def fetch(*args, **kwargs):
                return []
            return fetch

    facts._CACHE.clear()
    monkeypatch.setattr(facts, "FinMindProvider", _Provider)

    monkeypatch.setattr(facts, "market_today", lambda market: date(2026, 7, 21))
    await facts.build_tw_facts("2330")
    monkeypatch.setattr(facts, "market_today", lambda market: date(2026, 7, 22))
    await facts.build_tw_facts("2330")

    assert all(day == date(2026, 7, 22) for _, day in facts._CACHE)


async def test_etf_skips_financial_statement_requests(monkeypatch):
    calls = {"n": 0}

    class _Provider:
        def __getattr__(self, name):
            async def fetch(*args, **kwargs):
                calls["n"] += 1
                return []
            return fetch

    facts._CACHE.clear()
    facts._ACTIVE_ETF_CACHE.clear()
    monkeypatch.setattr(facts, "FinMindProvider", _Provider)
    monkeypatch.setattr(facts, "market_today", lambda market: date(2026, 7, 22))

    await facts.build_tw_facts("0050", is_etf=True)

    # ETF 不抓月營收與三張財報，但會多抓一次主動式 ETF 清單
    assert calls["n"] == 6
