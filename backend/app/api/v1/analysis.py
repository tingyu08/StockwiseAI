from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.core.exceptions import NotFoundError
from app.models import Stock
from app.services import analysis_service

router = APIRouter(tags=["analysis"])


def _get_stock(db: Session, market: str, symbol: str) -> Stock:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"尚未追蹤 {market}/{symbol}")
    return stock


@router.get("/analysis/overview", response_model=Envelope)
async def get_overview(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    """今日投資組合總評（無則 404，前端顯示產生按鈕）。"""
    from sqlalchemy import select as sa_select

    from app.models import AiOverview

    overview = db.execute(
        sa_select(AiOverview)
        .where(AiOverview.market == market)
        .order_by(AiOverview.trade_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if overview is None:
        raise NotFoundError("尚無總評，點「一鍵分析全部自選」產生")
    return ok(analysis_service.overview_dto(overview))


@router.post("/analysis/overview:run", response_model=Envelope)
async def run_overview(
    market: Literal["TW", "US"] = Query(...), db: Session = Depends(get_db)
) -> Envelope:
    """一鍵：全部自選批次分析＋總評（同一交易日快取，不重複扣額度）。"""
    overview = await analysis_service.run_overview(db, market)
    return ok(analysis_service.overview_dto(overview))


@router.get("/stocks/{symbol}/analysis", response_model=Envelope)
async def get_analysis(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    stock = _get_stock(db, market, symbol)
    report = analysis_service.latest_report(db, stock)
    if report is None:
        raise NotFoundError("尚無當日分析報告，可點「產生分析」")
    return ok(analysis_service.report_dto(report))


@router.post("/stocks/{symbol}/analysis:routine", response_model=Envelope)
async def run_routine(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    """單檔例行分析（走 flash-lite 降級鏈，含當日快取）。"""
    stock = _get_stock(db, market, symbol)
    await analysis_service.run_batch(db, [stock], kind="routine")
    report = analysis_service.latest_report(db, stock, kinds=("routine",))
    if report is None:
        raise NotFoundError("分析未產生結果，請稍後再試")
    return ok(analysis_service.report_dto(report))


@router.post("/stocks/{symbol}/analysis:deep", response_model=Envelope)
async def run_deep(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    db: Session = Depends(get_db),
) -> Envelope:
    """深度分析（3.5-flash，20 RPD 稀缺額度，額度盡回 429）。"""
    stock = _get_stock(db, market, symbol)
    report = await analysis_service.run_deep(db, stock)
    return ok(analysis_service.report_dto(report))
