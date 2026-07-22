"""台股專屬的籌碼面與基本面摘要（文字化後餵給 AI）。

只有台股：FinMind 免費層的美股僅 USStockPrice／USStockInfo 兩個資料集，
沒有法人、估值、融資券或營收資料（2026-07-22 實測）。

各項失敗時回傳空字串而非中斷分析——技術面本身仍可判讀；但一律留 warning
log，避免像先前 NAV 那樣無聲停更。
"""
import asyncio
import logging
from datetime import timedelta

from app.providers.market.finmind import FinMindProvider
from app.services.time_service import market_today

logger = logging.getLogger(__name__)

SHARES_PER_LOT = 1000  # FinMind 法人買賣超以股為單位

# FinMind 的法人別 → 一般揭露慣例的三大法人分類
INVESTOR_GROUPS: dict[str, tuple[str, ...]] = {
    "外資": ("Foreign_Investor", "Foreign_Dealer_Self"),
    "投信": ("Investment_Trust",),
    "自營商": ("Dealer_self", "Dealer_Hedging"),
}


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
    text = f"月營收 {last['revenue_year']}/{last['revenue_month']:02d} {revenue / 1e8:,.0f} 億元"
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


async def _safe(label: str, symbol: str, coro):
    try:
        return await coro
    except Exception:
        logger.warning("台股 %s 取得失敗（%s），本次分析略過該面向",
                       label, symbol, exc_info=True)
        return []


async def build_tw_facts(symbol: str, is_etf: bool = False) -> tuple[str, str]:
    """回傳 (籌碼面, 基本面) 兩段摘要。四個資料集併發抓取以壓低延遲。"""
    provider = FinMindProvider()
    today = market_today("TW")
    recent = today - timedelta(days=20)  # 約 10~13 個交易日

    tasks = [
        _safe("三大法人", symbol, provider.get_institutional_flows(symbol, recent, today)),
        _safe("融資融券", symbol, provider.get_margin_trading(symbol, recent, today)),
        _safe("估值", symbol, provider.get_valuation(symbol, today - timedelta(days=90), today)),
    ]
    # ETF 沒有月營收，不必多打一次 API
    if not is_etf:
        tasks.append(
            _safe("月營收", symbol,
                  provider.get_monthly_revenue(symbol, today - timedelta(days=430), today))
        )

    results = await asyncio.gather(*tasks)
    flows, margin, valuation = results[0], results[1], results[2]
    revenue = results[3] if len(results) > 3 else []

    flow_parts = [p for p in (build_flow_summary(flows), build_margin_summary(margin)) if p]
    fund_parts = [p for p in (build_valuation_summary(valuation),
                              build_revenue_summary(revenue)) if p]
    return "\n".join(flow_parts), "\n".join(fund_parts)
