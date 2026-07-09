"""AI 分析管線：輸入組裝 → 當日快取檢查 → AI → 落地。"""
import json
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import AiReport, DailyPrice, Indicator, Stock
from app.providers.ai import router as ai_router
from app.providers.ai.base import AnalysisContext
from app.providers.ai.gemini import PROMPT_VERSION
from app.providers.market.registry import get_provider

logger = logging.getLogger(__name__)


def latest_report(db: Session, stock: Stock, kinds: tuple[str, ...] = ("deep", "routine")) -> AiReport | None:
    """取最新報告（deep 優先於同日 routine）。"""
    for kind in kinds:
        report = db.execute(
            select(AiReport)
            .where(AiReport.stock_id == stock.id, AiReport.kind == kind)
            .order_by(AiReport.trade_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if report and report.trade_date == _last_trade_date(db, stock):
            return report
    return None


async def build_context(db: Session, stock: Stock) -> AnalysisContext:
    """後端組裝分析輸入——AI 不自己抓資料。"""
    since = date.today() - timedelta(days=180)
    prices = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id == stock.id, DailyPrice.date >= since)
        .order_by(DailyPrice.date)
    ).scalars().all()
    if len(prices) < 30:
        raise NotFoundError(f"{stock.symbol} 價格資料不足（<30 筆），請先同步")

    ind = db.execute(
        select(Indicator)
        .where(Indicator.stock_id == stock.id)
        .order_by(Indicator.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    last = prices[-1]
    closes = [float(p.close) for p in prices if p.close is not None]
    high_60 = max(closes[-60:])
    low_60 = min(closes[-60:])
    chg = {
        "5d": _pct(closes, 5), "20d": _pct(closes, 20), "60d": _pct(closes, 60),
    }
    vol_recent = sum(p.volume or 0 for p in prices[-5:]) / 5
    vol_prior = sum(p.volume or 0 for p in prices[-25:-5]) / 20 or 1

    lines = [
        f"名稱：{stock.name}（{'ETF' if stock.kind == 'etf' else '個股'}）",
        f"最新收盤（{last.date}）：{last.close} {stock.currency}",
        f"漲跌：5日 {chg['5d']:+.1f}%、20日 {chg['20d']:+.1f}%、60日 {chg['60d']:+.1f}%",
        f"60日高低：{high_60} / {low_60}（現價位於 {(closes[-1]-low_60)/(high_60-low_60)*100 if high_60>low_60 else 50:.0f}% 位置）",
        f"5日均量/20日均量比：{vol_recent/vol_prior:.2f}",
    ]
    if ind:
        lines.append(
            f"指標：MA5={_f(ind.ma5)} MA20={_f(ind.ma20)} MA60={_f(ind.ma60)}"
            f"｜RSI14={_f(ind.rsi14)}｜K={_f(ind.kd_k)} D={_f(ind.kd_d)}"
            f"｜MACD={_f(ind.macd)} Signal={_f(ind.macd_signal)}"
            f"｜布林上下軌={_f(ind.bb_upper)}/{_f(ind.bb_lower)}"
        )

    flow_summary = ""
    if stock.market == "TW":
        flow_summary = await _tw_flow_summary(stock)

    return AnalysisContext(
        symbol=stock.symbol,
        market=stock.market,
        price_summary="\n".join(lines),
        flow_summary=flow_summary,
    )


async def run_batch(db: Session, stocks: list[Stock], kind: str = "routine") -> dict:
    """批次分析（每批 ≤8 檔）。已有當日報告的股票自動跳過（快取）。"""
    trade_dates = {s.id: _last_trade_date(db, s) for s in stocks}
    pending = [
        s for s in stocks
        if trade_dates[s.id] and not _report_exists(db, s.id, trade_dates[s.id], kind)
    ]
    if not pending:
        return {"analyzed": 0, "skipped": len(stocks), "model": None}

    analyzed = 0
    model_used = None
    for i in range(0, len(pending), 8):
        batch = pending[i : i + 8]
        contexts = [await build_context(db, s) for s in batch]
        result, model_used = await ai_router.analyze_batch(db, contexts)
        by_symbol = {r.symbol: r for r in result.reports}
        for stock in batch:
            report = by_symbol.get(stock.symbol)
            if report is None:
                logger.warning("批次回應缺少 %s，跳過", stock.symbol)
                continue
            db.add(
                AiReport(
                    stock_id=stock.id,
                    trade_date=trade_dates[stock.id],
                    provider="gemini",
                    model=model_used,
                    prompt_version=PROMPT_VERSION,
                    kind=kind,
                    action=report.action,
                    confidence=report.confidence,
                    payload_json=report.model_dump_json(),
                )
            )
            analyzed += 1
        db.commit()
    return {"analyzed": analyzed, "skipped": len(stocks) - len(pending), "model": model_used}


async def run_deep(db: Session, stock: Stock) -> AiReport:
    """單檔深度分析（使用者觸發）。當日已有 deep 報告直接回傳（快取）。"""
    trade_date = _last_trade_date(db, stock)
    if trade_date is None:
        raise NotFoundError(f"{stock.symbol} 尚無價格資料")
    existing = db.execute(
        select(AiReport).where(
            AiReport.stock_id == stock.id,
            AiReport.trade_date == trade_date,
            AiReport.kind == "deep",
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    context = await build_context(db, stock)
    report, model = await ai_router.analyze_deep(db, context)
    row = AiReport(
        stock_id=stock.id,
        trade_date=trade_date,
        provider="gemini",
        model=model,
        prompt_version=PROMPT_VERSION,
        kind="deep",
        action=report.action,
        confidence=report.confidence,
        payload_json=report.model_dump_json(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def run_overview(db: Session, market: str) -> "AiOverview":
    """一鍵：全部自選批次分析（快取）→ 綜合成投資組合總評（當日快取）。"""
    from app.models import AiOverview, WatchlistItem

    stocks = db.execute(
        select(Stock)
        .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
        .where(Stock.market == market)
    ).scalars().all()
    if not stocks:
        raise NotFoundError("自選清單為空，請先加入股票")

    trade_date = max(
        (d for s in stocks if (d := _last_trade_date(db, s)) is not None), default=None
    )
    if trade_date is None:
        raise NotFoundError("尚無價格資料，請先同步")

    existing = db.execute(
        select(AiOverview).where(AiOverview.market == market, AiOverview.trade_date == trade_date)
    ).scalar_one_or_none()
    if existing:
        return existing

    # 1) 確保每檔都有當日例行報告（已有的會被快取跳過）
    await run_batch(db, stocks, kind="routine")

    # 2) 收集各股報告摘要，一次呼叫產生總評
    lines = []
    for stock in stocks:
        report = latest_report(db, stock, kinds=("deep", "routine"))
        if report is None:
            continue
        payload = json.loads(report.payload_json)
        lines.append(
            f"- {stock.symbol} {stock.name}：{payload['action']}"
            f"（信心 {payload['confidence']:.0%}）｜{payload['reasoning'][:80]}"
        )
    if not lines:
        raise NotFoundError("尚無任何個股分析報告可供總評")

    from app.providers.ai.router import generate_structured
    from app.providers.ai.schemas import OverviewReport

    prompt = (
        f"以下是我{'台股' if market == 'TW' else '美股'}自選清單中各股票的今日 AI 分析摘要，"
        f"請以投資組合的角度給出整體總評：\n\n" + "\n".join(lines)
    )
    result, model = await generate_structured(db, prompt, OverviewReport)

    overview = AiOverview(
        market=market, trade_date=trade_date, model=model,
        payload_json=result.model_dump_json(),
    )
    db.add(overview)
    db.commit()
    db.refresh(overview)
    return overview


def overview_dto(overview) -> dict:
    return {
        "market": overview.market,
        "trade_date": overview.trade_date.isoformat(),
        "model": overview.model,
        "report": json.loads(overview.payload_json),
        "created_at": overview.created_at.isoformat() if overview.created_at else None,
    }


def report_dto(report: AiReport) -> dict:
    return {
        "trade_date": report.trade_date.isoformat(),
        "kind": report.kind,
        "model": report.model,
        "prompt_version": report.prompt_version,
        "report": json.loads(report.payload_json),
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


# ---- helpers ----

def _pct(closes: list[float], days: int) -> float:
    if len(closes) <= days:
        return 0.0
    base = closes[-days - 1]
    return (closes[-1] - base) / base * 100 if base else 0.0


def _f(v) -> str:
    return f"{float(v):.2f}" if v is not None else "N/A"


def _last_trade_date(db: Session, stock: Stock) -> date | None:
    return db.execute(
        select(DailyPrice.date)
        .where(DailyPrice.stock_id == stock.id)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).scalar_one_or_none()


def _report_exists(db: Session, stock_id: int, trade_date: date, kind: str) -> bool:
    return (
        db.execute(
            select(AiReport.id).where(
                AiReport.stock_id == stock_id,
                AiReport.trade_date == trade_date,
                AiReport.kind == kind,
            )
        ).scalar_one_or_none()
        is not None
    )


async def _tw_flow_summary(stock: Stock) -> str:
    try:
        provider = get_provider("TW")
        rows = await provider.get_institutional_flows(
            stock.symbol, date.today() - timedelta(days=14), date.today()
        )
        if not rows:
            return ""
        net = sum(r.get("buy", 0) - r.get("sell", 0) for r in rows)
        return f"近 10 個交易日三大法人合計{'買超' if net >= 0 else '賣超'} {abs(net) / 1000:,.0f} 張"
    except Exception:
        logger.warning("法人資料取得失敗，略過籌碼面", exc_info=True)
        return ""
