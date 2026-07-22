"""AIProvider 抽象介面與降級鏈 Router。

模型分層（docs/PLAN.md §4.0）：
  例行批次   gemini-3.5-flash-lite（無備援模型）
  重要任務   gemini-3.6-flash 優先，額度不足降級至 flash-lite
             （交易決策、每日簡報）
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.providers.ai.schemas import BatchAnalysisResult


@dataclass(frozen=True)
class AnalysisContext:
    """後端組裝好的分析輸入（AI 不自己抓資料）。"""

    symbol: str
    market: str
    price_summary: str  # 近 120 日 OHLCV＋指標摘要（文字化）
    flow_summary: str = ""  # 籌碼面：三大法人分項買賣超＋融資券（僅台股）
    fundamental_summary: str = ""  # 基本面：本益比/淨值比/殖利率＋月營收（僅台股）
    premium_summary: str = ""  # ETF 折溢價現況
    news_summary: str = ""  # Antigravity 產出的新聞摘要


class AIProvider(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    async def analyze_batch(self, contexts: list[AnalysisContext]) -> BatchAnalysisResult:
        """批次分析多檔（例行）。實作須記錄 ai_usage_log。"""
        ...


# 註：實際的降級鏈是 app/providers/ai/router.py 的模組層函式
# （ROUTINE_CHAIN / PREMIUM_CHAIN）。此處原有一個從未被使用的 AIRouter
# 類別，會讓人誤以為存在第二套 router，已移除。
