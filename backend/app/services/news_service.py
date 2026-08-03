"""新聞面研究：抓真實新聞清單 → AI 摘要落地 → 餵給主分析管線。

- 存於 ai_reports（kind='news'），以「日曆日」為快取鍵——新聞跟今天有關，
  與交易日無關（週末/盤前也能跑），DB 唯一約束保證同日不重跑
- 摘要只作為 Gemini 主管線的 news_summary 輸入，不直接驅動下單

「找新聞」與「摘要新聞」是分開的（見 providers/news_feed）：Google 的搜尋
接地與 Antigravity agent 都因帳號資格問題不可用，但純文字生成完全正常。
拆開之後 AI 也無法虛構出處——網址是我們給它的。
"""
import json
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AiReport, Stock
from app.providers import news_feed
from app.providers.ai import router as ai_router
from app.providers.ai.schemas import NewsBrief
from app.services.time_service import market_today

logger = logging.getLogger(__name__)

FRESH_DAYS = 4  # 超過 4 天的新聞摘要不再注入分析（跨週末仍可用）


async def run_news_research(
    db: Session, stock: Stock, force: bool = False
) -> AiReport:
    """對單檔跑新聞研究。當日已有結果直接回傳（快取，不重複扣額度）。"""
    today = market_today(stock.market)
    existing = _get_report(db, stock.id, since=today)
    if existing and not force:
        return existing

    items = await news_feed.fetch_headlines(stock.symbol, stock.name, stock.market)
    if items:
        brief, model = await ai_router.generate_structured(
            db, _summary_prompt(stock, items), NewsBrief
        )
        summary = _render(brief)[:2000]
    else:
        # 沒有新聞就如實記錄，不要為了產出而叫 AI 生一段話
        summary, model = "近 7 天無重大新聞", "none"
        logger.info("新聞研究 %s：來源皆無資料", stock.symbol)

    if existing is None:
        row = AiReport(stock_id=stock.id, trade_date=today, kind="news")
        db.add(row)
    else:
        row = existing
    row.provider = "gemini"
    row.model = model
    row.prompt_version = "news-v3"
    row.input_hash = ""
    row.action = None
    row.confidence = None
    row.payload_json = json.dumps({"summary": summary}, ensure_ascii=False)
    try:
        db.commit()
    except IntegrityError:
        # 併發（手動觸發撞上排程）已寫入同一份（stock_id, trade_date, news）。
        # 不 rollback 的話 session 進入 PendingRollbackError 狀態，
        # news_research_daily 迴圈中後續每一檔都會連帶失敗。
        db.rollback()
        existing = _get_report(db, stock.id, since=today)
        if existing is None:
            raise
        return existing
    db.refresh(row)
    return row


def latest_news_report(db: Session, stock: Stock) -> AiReport | None:
    """最近一次（保鮮期內）的新聞研究報告；過期或不存在回 None。"""
    return _get_report(
        db, stock.id, since=market_today(stock.market) - timedelta(days=FRESH_DAYS)
    )


def latest_news_summary(db: Session, stock: Stock) -> str:
    """取最近的新聞摘要供分析管線注入；過期或不存在回空字串。"""
    report = latest_news_report(db, stock)
    if report is None:
        return ""
    summary = json.loads(report.payload_json).get("summary", "")
    return f"（{report.trade_date.strftime('%m/%d')} 研究）{summary}" if summary else ""


def news_dto(report: AiReport) -> dict:
    return {
        "date": report.trade_date.isoformat(),
        "model": report.model,
        "summary": json.loads(report.payload_json).get("summary", ""),
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


def _summary_prompt(stock: Stock, items: list[news_feed.NewsItem]) -> str:
    lines = "\n".join(
        f"- {i.published}｜{i.title}（{i.source}｜{i.url}）" for i in items
    )
    return f"""以下是「{stock.name}（{stock.market} {stock.symbol}）」近 7 天的新聞標題，
由系統抓取，全部附有出處：

{lines}

請據此產出新聞面研究：
- tone：整體基調（偏多／偏空／中性）
- tone_reason：一句話說明理由
- highlights：挑出 2~5 則最重要的，date 用 MM/DD，summary 一句話，
  source 與 url 必須原封不動沿用上方提供的值

只根據上方清單判斷，不要加入清單以外的資訊，也不要給投資建議。"""


def _render(brief: NewsBrief) -> str:
    """轉成與舊版相同的純文字格式，下游（分析管線、前端）不需改動。"""
    lines = [f"{brief.tone}——{brief.tone_reason}"]
    lines += [
        f"{h.date} {h.summary}（{h.source}｜{h.url}）" for h in brief.highlights
    ]
    return "\n".join(lines)


def _get_report(db: Session, stock_id: int, since: date) -> AiReport | None:
    return db.execute(
        select(AiReport)
        .where(
            AiReport.stock_id == stock_id,
            AiReport.kind == "news",
            AiReport.trade_date >= since,
        )
        .order_by(AiReport.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
