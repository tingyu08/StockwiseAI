"""AIProvider 抽象介面與降級鏈 Router。

降級順序（docs/PLAN.md §4.0）：
  例行批次   gemini-3.5-flash-lite（無備援模型）
  深度分析   gemini-3.6-flash（額度盡即拒絕，不降級——品質不可替代）
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.providers.ai.schemas import AnalysisReport, BatchAnalysisResult


@dataclass(frozen=True)
class AnalysisContext:
    """後端組裝好的分析輸入（AI 不自己抓資料）。"""

    symbol: str
    market: str
    price_summary: str  # 近 120 日 OHLCV＋指標摘要（文字化）
    flow_summary: str = ""  # 法人買賣超（台股）或量價位置（美股）
    premium_summary: str = ""  # ETF 折溢價現況
    news_summary: str = ""  # Antigravity 產出的新聞摘要


class AIProvider(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    async def analyze_batch(self, contexts: list[AnalysisContext]) -> BatchAnalysisResult:
        """批次分析多檔（例行）。實作須記錄 ai_usage_log。"""
        ...

    @abstractmethod
    async def analyze_deep(self, context: AnalysisContext) -> AnalysisReport:
        """單檔深度分析。"""
        ...


class AIRouter:
    """依額度狀態在 provider 鏈中自動降級。Phase 2 實作。"""

    def __init__(self, chain: list[AIProvider]):
        if not chain:
            raise ValueError("AI provider chain must not be empty")
        self.chain = chain
