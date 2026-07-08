"""ETF 折溢價：每日快照入庫（歷史由排程逐日累積——免費源無歷史 NAV）。

台股：mis.twse.com.tw all_etf.txt（全 ETF 一次回傳：預估淨值/市價/折溢價）
美股：yfinance Ticker.info 的 navPrice
premium_pct = (市價 - 淨值) / 淨值 × 100（正=溢價、負=折價）
"""
import asyncio
import logging
from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import UpstreamError
from app.models import EtfNav, Stock

logger = logging.getLogger(__name__)

TW_ETF_URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"


async def snapshot_premiums(db: Session, market: str) -> dict:
    """對該市場所有已追蹤 ETF 抓當日淨值快照。"""
    etfs = db.execute(
        select(Stock).where(Stock.market == market, Stock.kind == "etf")
    ).scalars().all()
    if not etfs:
        return {"market": market, "updated": 0, "note": "無追蹤中的 ETF"}

    if market == "TW":
        rows = await _tw_snapshot({e.symbol for e in etfs})
    else:
        rows = await _us_snapshot([e.symbol for e in etfs])

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


async def _us_snapshot(symbols: list[str]) -> dict[str, tuple[float, float, date]]:
    import yfinance as yf

    def _one(symbol: str) -> tuple[float, float, date] | None:
        try:
            info = yf.Ticker(symbol).info
            nav = info.get("navPrice")
            price = info.get("regularMarketPrice")
            if nav and price:
                return float(nav), float(price), date.today()
        except Exception as exc:
            logger.warning("yfinance nav %s failed: %s", symbol, exc)
        return None

    results = await asyncio.gather(*(asyncio.to_thread(_one, s) for s in symbols))
    return {s: r for s, r in zip(symbols, results) if r is not None}


def premium_list(db: Session, market: str) -> list[dict]:
    """各追蹤 ETF 的最新折溢價（無資料者標示 not available）。"""
    etfs = db.execute(
        select(Stock).where(Stock.market == market, Stock.kind == "etf")
    ).scalars().all()
    out = []
    for etf in etfs:
        latest = db.execute(
            select(EtfNav)
            .where(EtfNav.stock_id == etf.id)
            .order_by(EtfNav.date.desc())
            .limit(1)
        ).scalar_one_or_none()
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
