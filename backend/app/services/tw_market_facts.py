"""台股專屬的籌碼面與基本面摘要（文字化後餵給 AI）。

只有台股：FinMind 免費層的美股僅 USStockPrice／USStockInfo 兩個資料集，
沒有法人、估值、財報或營收資料（2026-07-22 依 llms-full.txt 覆核）。

**快取**：晨間會跑兩次批次（06:40 例行、07:10 交易決策），抓的是同一天的
同一份資料；季報更是一季才變一次。以 (代號, 市場日期) 為鍵快取，可將
FinMind 請求量減半，手動觸發分析也不再重複計費（上限 600 次/小時）。

各項失敗時略過該面向而非中斷分析——技術面本身仍可判讀；但一律留 warning
log，避免像先前 NAV 那樣無聲停更。
"""
import asyncio
import logging
from datetime import date, timedelta

from app.providers.market.finmind import FinMindProvider
from app.services.time_service import market_today

logger = logging.getLogger(__name__)

SHARES_PER_LOT = 1000  # FinMind 法人買賣超以股為單位
HUNDRED_MILLION = 1e8  # 億元

# FinMind 的法人別 → 一般揭露慣例的三大法人分類
INVESTOR_GROUPS: dict[str, tuple[str, ...]] = {
    "外資": ("Foreign_Investor", "Foreign_Dealer_Self"),
    "投信": ("Investment_Trust",),
    "自營商": ("Dealer_self", "Dealer_Hedging"),
}

# (籌碼面, 基本面) 摘要快取，鍵為 (symbol, 市場日期)
_CACHE: dict[tuple[str, date], tuple[str, str]] = {}
_ACTIVE_ETF_CACHE: dict[date, set[str]] = {}


# ---- 籌碼面 ----

def _net_lots(rows: list[dict]) -> float:
    return sum(r.get("buy", 0) - r.get("sell", 0) for r in rows) / SHARES_PER_LOT


def _describe(net: float) -> str:
    return f"{'買超' if net >= 0 else '賣超'} {abs(net):,.0f} 張"


def _streak(daily_net: dict[str, float], period_net: float) -> str:
    """由最近日期往回數，同方向連續幾日（僅 2 日以上才值得提）。

    連續方向與期間淨額相反時改用「轉」字——期間賣超但最近連買，
    寫成「賣超 744 張（連2日買超）」會讀起來自相矛盾。
    """
    days = sorted(daily_net, reverse=True)
    if not days:
        return ""
    sign = 1 if daily_net[days[0]] >= 0 else -1
    count = 0
    for day in days:
        value = daily_net[day]
        if value == 0 or (1 if value >= 0 else -1) != sign:
            break
        count += 1
    if count < 2:
        return ""
    direction = "買超" if sign > 0 else "賣超"
    same_way = (period_net >= 0) == (sign > 0)
    return f"（連{count}日{direction}）" if same_way else f"（最近{count}日轉{direction}）"


def build_flow_summary(rows: list[dict]) -> str:
    """三大法人分項買賣超＋連續天數。rows 為 FinMind 原始逐日逐法人資料。"""
    if not rows:
        return ""
    parts = []
    for label, names in INVESTOR_GROUPS.items():
        group = [r for r in rows if r.get("name") in names]
        if not group:
            continue
        daily: dict[str, float] = {}
        for r in group:
            daily[r["date"]] = daily.get(r["date"], 0) + (
                r.get("buy", 0) - r.get("sell", 0)
            ) / SHARES_PER_LOT
        net = _net_lots(group)
        parts.append(f"{label}{_describe(net)}{_streak(daily, net)}")
    if not parts:
        return ""
    days = len({r["date"] for r in rows})
    return f"三大法人近 {days} 個交易日：" + "、".join(parts)


def build_margin_summary(rows: list[dict]) -> str:
    """融資餘額變化與券資比——散戶槓桿與空方壓力。"""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: r["date"])
    last, first = rows[-1], rows[0]
    margin = last.get("MarginPurchaseTodayBalance") or 0
    short = last.get("ShortSaleTodayBalance") or 0
    base = first.get("MarginPurchaseTodayBalance") or 0
    parts = [f"融資餘額 {margin:,.0f} 張"]
    if base:
        parts[0] += f"（近 {len(rows)} 日 {(margin - base) / base * 100:+.1f}%）"
    parts.append(f"融券餘額 {short:,.0f} 張")
    if margin:
        parts.append(f"券資比 {short / margin * 100:.2f}%")
    return "、".join(parts)


def build_short_sale_summary(rows: list[dict]) -> str:
    """借券賣出（SBL）餘額——融券之外的另一條放空管道，法人常用。"""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: r["date"])
    last, first = rows[-1], rows[0]
    balance = last.get("SBLShortSalesCurrentDayBalance") or 0
    if not balance:
        return ""
    text = f"借券賣出餘額 {balance / SHARES_PER_LOT:,.0f} 張"
    base = first.get("SBLShortSalesCurrentDayBalance") or 0
    if base:
        text += f"（近 {len(rows)} 日 {(balance - base) / base * 100:+.1f}%）"
    return text


def build_suspension_summary(rows: list[dict], today: date) -> str:
    """僅在暫停融券期間仍有效時提示——除權息與軋空管制都會觸發。"""
    if not rows:
        return ""
    active = [
        r for r in rows
        if r.get("end_date") and str(r["end_date"]) >= today.isoformat()
    ]
    if not active:
        return ""
    r = active[0]
    return f"暫停融券 {r['date']}~{r['end_date']}（{r.get('reason', '未載明')}）"


# ---- 基本面 ----

def build_valuation_summary(rows: list[dict]) -> str:
    """本益比／股價淨值比／殖利率，並標示 PER 在近期區間的相對位置。"""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: r["date"])
    last = rows[-1]
    per, pbr = last.get("PER"), last.get("PBR")
    yield_ = last.get("dividend_yield")
    parts = []
    if per:
        pers = [r["PER"] for r in rows if r.get("PER")]
        span = f"，近 {len(pers)} 日區間 {min(pers):.1f}~{max(pers):.1f}" if pers else ""
        parts.append(f"本益比 {per:.1f}{span}")
    if pbr:
        parts.append(f"股價淨值比 {pbr:.2f}")
    if yield_:
        parts.append(f"現金殖利率 {yield_:.2f}%")
    return "、".join(parts)


def build_revenue_summary(rows: list[dict]) -> str:
    """最新月營收與月增／年增——ETF 無此資料，個股才有。"""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: (r["revenue_year"], r["revenue_month"]))
    last = rows[-1]
    revenue = last.get("revenue") or 0
    if not revenue:
        return ""
    text = (f"月營收 {last['revenue_year']}/{last['revenue_month']:02d} "
            f"{revenue / HUNDRED_MILLION:,.0f} 億元")
    if len(rows) >= 2 and (prev := rows[-2].get("revenue")):
        text += f"，月增 {(revenue - prev) / prev * 100:+.1f}%"
    year_ago = next(
        (
            r["revenue"]
            for r in rows
            if r["revenue_year"] == last["revenue_year"] - 1
            and r["revenue_month"] == last["revenue_month"]
            and r.get("revenue")
        ),
        None,
    )
    if year_ago:
        text += f"，年增 {(revenue - year_ago) / year_ago * 100:+.1f}%"
    return text


def _by_quarter(rows: list[dict]) -> dict[str, dict[str, float]]:
    """long format（date/type/value）轉成 {季別: {欄位: 值}}。"""
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("value") is not None:
            out.setdefault(r["date"], {})[r["type"]] = r["value"]
    return out


def build_income_summary(rows: list[dict]) -> str:
    """季報獲利品質：毛利率／營益率／淨利率＋EPS，並與上一季比較。"""
    quarters = _by_quarter(rows)
    if not quarters:
        return ""
    keys = sorted(quarters)
    latest = quarters[keys[-1]]
    revenue = latest.get("Revenue") or 0
    if not revenue:
        return ""

    def margin(field: str) -> float | None:
        value = latest.get(field)
        return value / revenue * 100 if value is not None else None

    parts = [f"財報 {keys[-1]}：營收 {revenue / HUNDRED_MILLION:,.0f} 億元"]
    for label, field in (("毛利率", "GrossProfit"), ("營益率", "OperatingIncome"),
                         ("淨利率", "IncomeAfterTaxes")):
        if (m := margin(field)) is not None:
            parts.append(f"{label} {m:.1f}%")
    if (eps := latest.get("EPS")) is not None:
        text = f"EPS {eps:.2f} 元"
        if len(keys) >= 2 and (prev_eps := quarters[keys[-2]].get("EPS")):
            text += f"（前季 {prev_eps:.2f}）"
        parts.append(text)
    return "、".join(parts)


def build_balance_summary(rows: list[dict]) -> str:
    """財務體質：負債比與流動比——判斷有沒有踩雷風險。"""
    quarters = _by_quarter(rows)
    if not quarters:
        return ""
    latest = quarters[sorted(quarters)[-1]]
    parts = []
    assets, liabilities = latest.get("TotalAssets"), latest.get("Liabilities")
    if assets and liabilities is not None:
        parts.append(f"負債比 {liabilities / assets * 100:.1f}%")
    current_a, current_l = latest.get("CurrentAssets"), latest.get("CurrentLiabilities")
    if current_a and current_l:
        parts.append(f"流動比 {current_a / current_l:.2f}")
    if cash := latest.get("CashAndCashEquivalents"):
        parts.append(f"現金 {cash / HUNDRED_MILLION:,.0f} 億元")
    return "、".join(parts)


def build_cashflow_summary(rows: list[dict], income_rows: list[dict]) -> str:
    """盈餘品質：營運現金流相對稅後淨利的倍數（明顯低於 1 常是警訊）。"""
    quarters = _by_quarter(rows)
    if not quarters:
        return ""
    latest = quarters[sorted(quarters)[-1]]
    ocf = latest.get("NetCashInflowFromOperatingActivities")
    if ocf is None:
        return ""
    text = f"營運現金流 {ocf / HUNDRED_MILLION:,.0f} 億元"
    income_q = _by_quarter(income_rows)
    if income_q:
        net = income_q[sorted(income_q)[-1]].get("IncomeAfterTaxes")
        if net and net > 0:
            text += f"（為稅後淨利 {ocf / net:.2f} 倍）"
    return text


# ---- 組裝 ----

async def _safe(label: str, symbol: str, coro):
    try:
        return await coro
    except Exception:
        logger.warning("台股 %s 取得失敗（%s），本次分析略過該面向",
                       label, symbol, exc_info=True)
        return []


async def _active_etf_symbols(provider: FinMindProvider, today: date) -> set[str]:
    """主動式 ETF 清單全體共用，每日抓一次即可。"""
    if today not in _ACTIVE_ETF_CACHE:
        _ACTIVE_ETF_CACHE.clear()
        rows = await _safe("主動式ETF清單", "-", provider.get_active_etf_list())
        _ACTIVE_ETF_CACHE[today] = {r["stock_id"] for r in rows if r.get("stock_id")}
    return _ACTIVE_ETF_CACHE[today]


async def build_tw_facts(symbol: str, is_etf: bool = False) -> tuple[str, str]:
    """回傳 (籌碼面, 基本面) 兩段摘要。同一交易日同一檔只實際抓取一次。"""
    today = market_today("TW")
    if (cached := _CACHE.get((symbol, today))) is not None:
        return cached

    provider = FinMindProvider()
    recent = today - timedelta(days=20)  # 約 10~13 個交易日
    quarters_since = today - timedelta(days=400)  # 涵蓋至少 4 季

    tasks = [
        _safe("三大法人", symbol, provider.get_institutional_flows(symbol, recent, today)),
        _safe("融資融券", symbol, provider.get_margin_trading(symbol, recent, today)),
        _safe("借券賣出", symbol, provider.get_short_sale_balances(symbol, recent)),
        _safe("暫停融券", symbol, provider.get_short_sale_suspension(symbol, recent)),
        _safe("估值", symbol, provider.get_valuation(symbol, today - timedelta(days=90), today)),
    ]
    # ETF 沒有財報與月營收，不必多打四次 API
    if not is_etf:
        tasks += [
            _safe("月營收", symbol,
                  provider.get_monthly_revenue(symbol, today - timedelta(days=430), today)),
            _safe("損益表", symbol, provider.get_income_statement(symbol, quarters_since)),
            _safe("資產負債表", symbol, provider.get_balance_sheet(symbol, quarters_since)),
            _safe("現金流量表", symbol, provider.get_cash_flows(symbol, quarters_since)),
        ]

    results = await asyncio.gather(*tasks)
    flows, margin, short_sale, suspension, valuation = results[:5]
    revenue, income, balance, cashflow = (results[5:] if not is_etf else ([],) * 4)

    flow_parts = [
        build_flow_summary(flows),
        build_margin_summary(margin),
        build_short_sale_summary(short_sale),
        build_suspension_summary(suspension, today),
    ]
    fund_parts = [
        build_valuation_summary(valuation),
        build_revenue_summary(revenue),
        build_income_summary(income),
        build_balance_summary(balance),
        build_cashflow_summary(cashflow, income),
    ]
    if is_etf and symbol in await _active_etf_symbols(provider, today):
        fund_parts.insert(0, "本檔為主動式 ETF（由經理人選股，非追蹤指數）")

    result = ("\n".join(p for p in flow_parts if p),
              "\n".join(p for p in fund_parts if p))
    # 只保留當日份，跨日自動淘汰（長駐後端，不清會無限成長）
    for key in [k for k in _CACHE if k[1] != today]:
        del _CACHE[key]
    _CACHE[(symbol, today)] = result
    return result
