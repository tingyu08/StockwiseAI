"""AI 分析管線：輸入組裝 → 當日快取檢查 → AI → 落地。"""
import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import AiReport, DailyPrice, Indicator, Stock
from app.providers.ai import router as ai_router
from app.providers.ai.base import AnalysisContext
from app.providers.ai.gemini import PROMPT_VERSION
from app.services.market_gateway import market_data

if TYPE_CHECKING:
    from app.models import AiOverview

logger = logging.getLogger(__name__)

# 同市場的總評一次只允許一個請求重跑，避免連按時重複扣 AI 額度
_overview_locks: dict[str, asyncio.Lock] = {}


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

    from app.services.news_service import latest_news_summary

    return AnalysisContext(
        symbol=stock.symbol,
        market=stock.market,
        price_summary="\n".join(lines),
        flow_summary=flow_summary,
        news_summary=latest_news_summary(db, stock),
    )


async def run_batch(db: Session, stocks: Sequence[Stock], kind: str = "routine") -> dict:
    """批次分析（每批 ≤8 檔）。輸入未變時命中快取。"""
    trade_dates = {s.id: _last_trade_date(db, s) for s in stocks}
    pending: list[tuple[Stock, AnalysisContext, str]] = []
    for stock in stocks:
        trade_date = trade_dates[stock.id]
        if trade_date is None:
            continue
        context = await build_context(db, stock)
        input_hash = analysis_input_hash(context, kind)
        if not _report_exists(db, stock.id, trade_date, kind, input_hash):
            pending.append((stock, context, input_hash))
    if not pending:
        return {"analyzed": 0, "skipped": len(stocks), "model": None}

    analyzed = 0
    model_used = None
    for i in range(0, len(pending), 8):
        batch = pending[i : i + 8]
        contexts = [item[1] for item in batch]
        analyze = (
            ai_router.analyze_trading_batch if kind == "trade" else ai_router.analyze_batch
        )
        result, model_used = await analyze(db, contexts)
        # 模型偶爾把 symbol 回成 'TW/2330' 或含名稱 → 正規化後比對；
        # 數量一致時再以順序比對兜底（批次 prompt 要求依序回傳）
        by_symbol = {_norm_symbol(r.symbol): r for r in result.reports}
        rows: list[dict] = []
        for idx, (stock, _context, input_hash) in enumerate(batch):
            report = by_symbol.get(_norm_symbol(stock.symbol))
            if report is None and len(result.reports) == len(batch):
                report = result.reports[idx]
                logger.warning("批次 symbol 不符（%s），以順序兜底", stock.symbol)
            if report is None:
                logger.warning("批次回應缺少 %s，跳過", stock.symbol)
                continue
            rows.append(
                dict(
                    stock_id=stock.id,
                    trade_date=trade_dates[stock.id],
                    provider="gemini",
                    model=model_used,
                    prompt_version=PROMPT_VERSION,
                    input_hash=input_hash,
                    kind=kind,
                    action=report.action,
                    confidence=report.confidence,
                    payload_json=report.model_dump_json(),
                )
            )
        analyzed += _insert_reports(db, rows)
    return {"analyzed": analyzed, "skipped": len(stocks) - len(pending), "model": model_used}


def _insert_reports(db: Session, rows: list[dict]) -> int:
    """Portable upsert keyed by the existing daily uniqueness constraint."""
    written = 0
    for row in rows:
        existing = db.execute(
            select(AiReport).where(
                AiReport.stock_id == row["stock_id"],
                AiReport.trade_date == row["trade_date"],
                AiReport.kind == row["kind"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.input_hash == row["input_hash"]:
                continue
            for key, value in row.items():
                setattr(existing, key, value)
        else:
            db.add(AiReport(**row))
        try:
            db.commit()
            written += 1
        except IntegrityError:
            db.rollback()
            concurrent = db.execute(
                select(AiReport).where(
                    AiReport.stock_id == row["stock_id"],
                    AiReport.trade_date == row["trade_date"],
                    AiReport.kind == row["kind"],
                )
            ).scalar_one()
            if concurrent.input_hash != row["input_hash"]:
                for key, value in row.items():
                    setattr(concurrent, key, value)
                db.commit()
                written += 1
    return written


async def run_deep(db: Session, stock: Stock) -> AiReport:
    """單檔深度分析（使用者觸發）。當日已有 deep 報告直接回傳（快取）。"""
    trade_date = _last_trade_date(db, stock)
    if trade_date is None:
        raise NotFoundError(f"{stock.symbol} 尚無價格資料")
    context = await build_context(db, stock)
    input_hash = analysis_input_hash(context, "deep")
    existing = db.execute(
        select(AiReport).where(
            AiReport.stock_id == stock.id,
            AiReport.trade_date == trade_date,
            AiReport.kind == "deep",
        )
    ).scalar_one_or_none()
    if existing and existing.input_hash == input_hash:
        return existing

    report, model = await ai_router.analyze_deep(db, context)
    values = {
        "provider": "gemini",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "input_hash": input_hash,
        "action": report.action,
        "confidence": report.confidence,
        "payload_json": report.model_dump_json(),
    }
    if existing is None:
        row = AiReport(
            stock_id=stock.id, trade_date=trade_date, kind="deep", **values
        )
        db.add(row)
    else:
        row = existing
        for key, value in values.items():
            setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError:
        # 並發請求已寫入同一份（stock_id, trade_date, deep）→ 視為快取命中
        db.rollback()
        return db.execute(
            select(AiReport).where(
                AiReport.stock_id == stock.id,
                AiReport.trade_date == trade_date,
                AiReport.kind == "deep",
            )
        ).scalar_one()
    db.refresh(row)
    return row


async def run_overview(db: Session, market: str, force: bool = False) -> "AiOverview":
    """一鍵：全部自選批次分析（快取）→ 四模組每日簡報（當日快取）。

    同市場以 asyncio.Lock 序列化：連按時第二個請求等第一個完成後直接命中快取。
    """
    lock = _overview_locks.setdefault(market, asyncio.Lock())
    async with lock:
        return await _run_overview(db, market, force=force)


async def _run_overview(db: Session, market: str, force: bool = False) -> "AiOverview":
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

    # 1) 確保每檔都有當日例行報告（已有的會被快取跳過）
    await run_batch(db, stocks, kind="routine")

    # 2) 市場環境（真實指數/ADR 數據，AI 只解讀不虛構）
    from app.services.market_context import build_market_context

    market_ctx = await build_market_context(market)

    # 3) 各股詳細摘要（昨日表現＋AI 報告全項）
    lines = []
    for stock in stocks:
        report = latest_report(db, stock, kinds=("deep", "routine"))
        chg = _yesterday_change(db, stock)
        if report is None:
            lines.append(f"- {stock.symbol} {stock.name}：{chg}｜（尚無 AI 報告）")
            continue
        p = json.loads(report.payload_json)
        lines.append(
            f"- {stock.symbol} {stock.name}：{chg}"
            f"｜AI={p['action']}（信心 {p['confidence']:.0%}）"
            f"｜目標 {p['target_price_low']}~{p['target_price_high']}、停損 {p['stop_loss']}"
            f"｜{p['reasoning'][:100]}"
        )

    local_name = "台股（加權指數）" if market == "TW" else "美股（S&P 500）"
    prompt = f"""請根據以下真實數據，產出一份今日投資簡報（四個模組）。

{market_ctx}

【自選標的現況與個股 AI 分析】
{chr(10).join(lines)}

要求：
- 模組1（global_market）：解讀上方全球指數與 ADR 數據，判斷風險情緒
- 模組2（local_market）：{local_name}的支撐/壓力必須引用上方提供的 MA20/MA60/近20日高低作為技術依據，並五選一預判今日走勢、給 2~3 個依據；法人資料若未提供請在 flow_comment 誠實說明
- 模組3（stock_notes）：為上方每一檔標的產出點評，關鍵價位以個股 AI 報告的目標/停損為基礎微調
- 模組4（risks）：只列出你有把握的重大事件（不確定的標註「時間請以官方公告為準」），黑天鵝觀察點與盤中監控訊號要具體可操作
- 所有數字必須來自提供的資料，不得虛構行情"""

    input_hash = _hash_text(f"{PROMPT_VERSION}\n{prompt}")
    existing = db.execute(
        select(AiOverview).where(
            AiOverview.market == market, AiOverview.trade_date == trade_date
        )
    ).scalar_one_or_none()
    if existing and existing.input_hash == input_hash and not force:
        return existing

    from app.providers.ai.router import generate_premium_structured
    from app.providers.ai.schemas import DailyBriefing

    result, model = await generate_premium_structured(db, prompt, DailyBriefing)

    if existing is None:
        overview = AiOverview(market=market, trade_date=trade_date)
        db.add(overview)
    else:
        overview = existing
    overview.model = model
    overview.prompt_version = PROMPT_VERSION
    overview.input_hash = input_hash
    overview.payload_json = result.model_dump_json()
    try:
        db.commit()
    except IntegrityError:
        # 撞 UNIQUE(market, trade_date)（例如多進程部署時鎖不跨進程）→ 回傳既有總評
        db.rollback()
        return db.execute(
            select(AiOverview).where(
                AiOverview.market == market, AiOverview.trade_date == trade_date
            )
        ).scalar_one()
    db.refresh(overview)
    return overview


def _yesterday_change(db: Session, stock: Stock) -> str:
    rows = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id == stock.id, DailyPrice.close.is_not(None))
        .order_by(DailyPrice.date.desc())
        .limit(2)
    ).scalars().all()
    closes = [float(r.close) for r in rows if r.close is not None]
    if len(closes) < 2:
        return "昨日資料不足"
    last, prev = closes
    return f"收盤 {last}（{(last - prev) / prev * 100:+.2f}%）"


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

def _norm_symbol(raw: str) -> str:
    """'TW/2330'、'2330 台積電' → '2330'。"""
    s = raw.strip().upper()
    if "/" in s:
        s = s.split("/")[-1]
    return s.split()[0] if s else s


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


def _report_exists(
    db: Session,
    stock_id: int,
    trade_date: date,
    kind: str,
    input_hash: str | None = None,
) -> bool:
    stmt = select(AiReport.id).where(
        AiReport.stock_id == stock_id,
        AiReport.trade_date == trade_date,
        AiReport.kind == kind,
    )
    if input_hash is not None:
        stmt = stmt.where(AiReport.input_hash == input_hash)
    return db.execute(stmt).scalar_one_or_none() is not None


def analysis_input_hash(context: AnalysisContext, kind: str) -> str:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "kind": kind,
        "context": asdict(context),
    }
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _tw_flow_summary(stock: Stock) -> str:
    try:
        rows = await market_data.get_institutional_flows(
            "TW", stock.symbol, date.today() - timedelta(days=14), date.today()
        )
        if not rows:
            return ""
        net = sum(r.get("buy", 0) - r.get("sell", 0) for r in rows)
        return f"近 10 個交易日三大法人合計{'買超' if net >= 0 else '賣超'} {abs(net) / 1000:,.0f} 張"
    except Exception:
        logger.warning("法人資料取得失敗，略過籌碼面", exc_info=True)
        return ""
