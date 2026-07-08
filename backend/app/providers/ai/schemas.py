"""AI 結構化輸出 schema — 所有 AI Provider 的統一回傳格式。

任何模型的輸出落地前必須通過這裡的 Pydantic 驗證；
驗證失敗由 provider 重試一次，再失敗即丟 UpstreamError。
"""
from typing import Literal

from pydantic import BaseModel, Field


class Scenario(BaseModel):
    target_price: float
    trigger_condition: str
    probability: float = Field(ge=0, le=1)


class Scenarios(BaseModel):
    bull: Scenario
    base: Scenario
    bear: Scenario


class AnalysisReport(BaseModel):
    """單檔股票的分析報告。"""

    symbol: str
    action: Literal["buy", "sell", "hold"]
    confidence: float = Field(ge=0, le=1)
    target_price_low: float
    target_price_high: float
    stop_loss: float
    reasoning: str
    scenarios: Scenarios
    risks: list[str]


class BatchAnalysisResult(BaseModel):
    """批次分析（一次請求多檔）的回傳。"""

    reports: list[AnalysisReport]


class NewsDigest(BaseModel):
    """Antigravity 新聞研究輸出（自由文字摘要＋來源）。"""

    symbol: str
    summary: str
    sources: list[str] = []
