"""ETF 折溢價：每日快照入庫（歷史由排程逐日累積——免費源無歷史 NAV）。

**僅支援台股**：淨值取自 mis.twse.com.tw all_etf.txt（全 ETF 一次回傳
預估淨值/市價/折溢價），且台股 ETF——特別是主動式——折溢價常達數個百分點，
是有決策價值的訊號。

美股已移除：免費資料源（FinMind 免費層、Nasdaq 公開 API）皆無 ETF 淨值，
僅 Yahoo 有而它封鎖機房 IP；且 VOO/QQQ 這類大型指數 ETF 有做市商全天套利，
實測折溢價約 -0.003%，資訊量趨近於零，不值得為此維護發行商級別的抓取。
premium_pct = (市價 - 淨值) / 淨值 × 100（正=溢價、負=折價）
"""
import logging
from datetime import date, datetime

import httpx
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import UpstreamError
from app.models import EtfNav, Stock

logger = logging.getLogger(__name__)

TW_ETF_URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"
SUPPORTED_MARKETS = ("TW",)


async def snapshot_premiums(db: Session, market: str) -> dict:
    """對該市場所有已追蹤 ETF 抓當日淨值快照（僅台股）。"""
    if market not in SUPPORTED_MARKETS:
        return {
            "market": market, "updated": 0,
            "skipped": "此市場不提供折溢價（免費資料源無 ETF 淨值）",
        }
    etfs = db.execute(
        select(Stock).where(Stock.market == market, Stock.kind == "etf")
    ).scalars().all()
    if not etfs:
        return {"market": market, "updated": 0, "note": "無追蹤中的 ETF"}

    rows = await _tw_snapshot({e.symbol for e in etfs})

    updated = 0
    for etf in etfs:
        data = rows.get(etf.symbol)
        if data is None:
            continue
        nav, close, snap_date = data
        if not nav or not close:
            continue
        existing = db.execute(
            select(EtfNav).where(EtfNav.stock_id == etf.id, EtfNav.date == snap_date)
        ).scalar_one_or_none()
        premium = round((close - nav) / nav * 100, 4)
        if existing:
            existing.nav, existing.close, existing.premium_pct = nav, close, premium
        else:
            db.add(EtfNav(stock_id=etf.id, date=snap_date, nav=nav, close=close, premium_pct=premium))
        updated += 1
    db.commit()
    logger.info("premium snapshot %s: %d/%d updated", market, updated, len(etfs))
    if updated == 0:
        # 有追蹤 ETF 卻一筆都沒更新＝上游整批失敗。
        # 必須讓工作失敗才會出現在通知裡——曾因此無聲停更一週（卡在 07-14）。
        raise UpstreamError(
            f"{market} NAV 快照零更新（{len(etfs)} 檔 ETF 全數抓取失敗）"
        )
    return {"market": market, "updated": updated, "total_etfs": len(etfs)}


async def _tw_snapshot(symbols: set[str]) -> dict[str, tuple[float, float, date]]:
    """全 ETF 快照：{symbol: (nav, price, date)}。"""
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as client:
        res = await client.get(TW_ETF_URL)
    if res.status_code != 200:
        raise UpstreamError(f"TWSE ETF 淨值端點回應 {res.status_code}")
    body = res.json()

    out: dict[str, tuple[float, float, date]] = {}
    groups: list[dict] = []
    for value in body.values():  # 結構：{"a1": [{"msgArray": [...]}, ...], ...}
        if isinstance(value, list):
            groups.extend(g for g in value if isinstance(g, dict))
        elif isinstance(value, dict):
            groups.append(value)
    for group in groups:
        for row in group.get("msgArray", []):
            symbol = row.get("a")
            if symbol not in symbols:
                continue
            try:
                nav = float(row["e"])  # 預估淨值
                price = float(row["f"])  # 市價
                snap_date = datetime.strptime(row["i"], "%Y%m%d").date()
            except (KeyError, TypeError, ValueError):
                continue
            out[symbol] = (nav, price, snap_date)
    return out


def premium_list(db: Session, market: str) -> list[dict]:
    """各追蹤 ETF 的最新折溢價（無資料者標示 not available）。"""
    latest_dates = (
        select(EtfNav.stock_id, func.max(EtfNav.date).label("latest_date"))
        .group_by(EtfNav.stock_id)
        .subquery()
    )
    rows = db.execute(
        select(Stock, EtfNav)
        .outerjoin(latest_dates, latest_dates.c.stock_id == Stock.id)
        .outerjoin(
            EtfNav,
            and_(
                EtfNav.stock_id == Stock.id,
                EtfNav.date == latest_dates.c.latest_date,
            ),
        )
        .where(Stock.market == market, Stock.kind == "etf")
    ).all()
    out = []
    for etf, latest in rows:
        out.append(
            {
                "symbol": etf.symbol,
                "name": etf.name,
                "date": latest.date.isoformat() if latest else None,
                "nav": float(latest.nav) if latest and latest.nav else None,
                "close": float(latest.close) if latest and latest.close else None,
                "premium_pct": float(latest.premium_pct) if latest and latest.premium_pct is not None else None,
            }
        )
    return sorted(out, key=lambda x: x["premium_pct"] if x["premium_pct"] is not None else -999, reverse=True)


def premium_history(db: Session, stock: Stock) -> list[dict]:
    rows = db.execute(
        select(EtfNav).where(EtfNav.stock_id == stock.id).order_by(EtfNav.date)
    ).scalars().all()
    return [
        {
            "date": r.date.isoformat(),
            "nav": float(r.nav) if r.nav else None,
            "close": float(r.close) if r.close else None,
            "premium_pct": float(r.premium_pct) if r.premium_pct is not None else None,
        }
        for r in rows
    ]
