from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.envelope import Envelope, ok
from app.services.backtest_service import STRATEGIES, run_backtest

router = APIRouter(tags=["backtest"])


class BacktestBody(BaseModel):
    market: Literal["TW", "US"]
    symbol: str = Field(min_length=1, max_length=16)
    strategy: Literal["ma_cross", "rsi_reversion", "bollinger"]
    range_days: int = Field(default=365, ge=120, le=1095)


@router.get("/backtest/strategies", response_model=Envelope)
async def list_strategies() -> Envelope:
    return ok([{"key": k, "desc": v} for k, v in STRATEGIES.items()])


@router.post("/backtest", response_model=Envelope)
async def backtest(body: BacktestBody, db: Session = Depends(get_db)) -> Envelope:
    return ok(run_backtest(db, body.market, body.symbol, body.strategy, body.range_days))
