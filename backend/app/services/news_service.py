"""新聞面研究：Antigravity 每日搜尋個股新聞 → 摘要落地 → 餵給主分析管線。

- 存於 ai_reports（kind='news'），以「日曆日」為快取鍵——新聞跟今天有關，
  與交易日無關（週末/盤前也能跑），DB 唯一約束保證同日不重跑
- 摘要為自由文字（Antigravity 不支援 structured output），
  只作為 Gemini 主管線的 news_summary 輸入，不直接驅動下單
"""
import json
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AiReport, Stock
from app.providers.ai.antigravity import AntigravityProvider

logger = logging.getLogger(__name__)

FRESH_DAYS = 4  # 超過 4 天的新聞摘要不再注入分析（跨週末仍可用）


async def run_news_research(db: Session, stock: Stock) -> AiReport:
    """對單檔跑新聞研究。當日已有結果直接回傳（快取，不重複扣額度）。"""
    today = date.today()
    existing = _get_report(db, stock.id, since=today)
    if existing:
        return existing

    provider = AntigravityProvider(db)
    summary = await provider.research_news(stock.symbol, stock.name, stock.market)

    row = AiReport(
        stock_id=stock.id,
        trade_date=today,
        provider=provider.provider_name,
        model=provider.model_name,
        prompt_version="news-v1",
        kind="news",
        action=None,
        confidence=None,
        payload_json=json.dumps({"summary": summary}, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def latest_news_report(db: Session, stock: Stock) -> AiReport | None:
    """最近一次（保鮮期內）的新聞研究報告；過期或不存在回 None。"""
    return _get_report(db, stock.id, since=date.today() - timedelta(days=FRESH_DAYS))


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
