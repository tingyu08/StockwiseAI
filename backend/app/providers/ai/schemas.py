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


class GlobalModule(BaseModel):
    """模組 1：全球盤勢總結。"""

    index_comments: list[str]  # 三大指數與費半動向解讀（每條一句）
    key_stocks_comment: str  # 關鍵權值股表現（含 ADR）
    risk_sentiment: Literal["risk_on", "risk_neutral", "risk_off"]
    one_liner: str  # 一句話總結


class LocalMarketModule(BaseModel):
    """模組 2：今日大盤預判（依提供的技術位階數據）。"""

    support: float  # 關鍵支撐（需有技術依據）
    resistance: float  # 關鍵壓力
    levels_rationale: str  # 支撐壓力的技術依據
    flow_comment: str  # 法人/籌碼解讀（無資料時說明）
    prediction: Literal["開高走高", "開高走低", "開低走高", "開低走低", "震盪整理"]
    prediction_rationales: list[str]  # 2~3 個依據


class StockNote(BaseModel):
    """模組 3：單一標的點評。"""

    symbol: str
    yesterday: str  # 昨日表現
    technical: str  # 短期技術面判斷
    action: Literal["買進", "持有", "減碼", "觀望"]
    rationale: str
    entry_price: float
    stop_loss: float
    target_price: float


class RiskModule(BaseModel):
    """模組 4：今日風險提示。"""

    events: list[str]  # 已知的重大事件/數據（不確定的要標註）
    black_swan_watch: list[str]  # 地緣政治或黑天鵝觀察點
    monitor_signals: list[str]  # 需要盤中特別監控的訊號


class DailyBriefing(BaseModel):
    """每日投資簡報（四模組）。"""

    global_market: GlobalModule
    local_market: LocalMarketModule
    stock_notes: list[StockNote]
    risks: RiskModule
    overall_stance: Literal["bullish", "neutral", "bearish"]


class NewsDigest(BaseModel):
    """Antigravity 新聞研究輸出（自由文字摘要＋來源）。"""

    symbol: str
    summary: str
    sources: list[str] = []
