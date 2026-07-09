"""AI 降級鏈 Router。

例行批次：flash-lite → gemma-4（額度盡或限流時自動降級）
深度分析：3.5-flash（不降級——額度盡即回報，品質不可替代）
"""
import logging

from sqlalchemy.orm import Session

from app.core.exceptions import QuotaExceededError, UpstreamError
from app.providers.ai.base import AnalysisContext
from app.providers.ai.gemini import GeminiProvider
from app.providers.ai.schemas import AnalysisReport, BatchAnalysisResult

logger = logging.getLogger(__name__)

ROUTINE_CHAIN = [
    ("gemini-3.1-flash-lite", True),
    ("gemma-4-31b-it", False),  # Gemma 不支援 response_schema
]
DEEP_MODEL = "gemini-3.5-flash"


async def analyze_batch(db: Session, contexts: list[AnalysisContext]) -> tuple[BatchAnalysisResult, str]:
    """回傳 (結果, 實際使用的模型)。"""
    last_error: Exception | None = None
    for model, use_schema in ROUTINE_CHAIN:
        try:
            provider = GeminiProvider(model, db, use_schema=use_schema)
            result = await provider.analyze_batch(contexts)
            return result, model
        except (QuotaExceededError, UpstreamError) as exc:
            logger.warning("批次分析 %s 失敗，降級下一層：%s", model, exc.message)
            last_error = exc
    raise UpstreamError("所有例行分析模型皆不可用") from last_error


async def analyze_deep(db: Session, context: AnalysisContext) -> tuple[AnalysisReport, str]:
    provider = GeminiProvider(DEEP_MODEL, db, use_schema=True)
    return await provider.analyze_deep(context), DEEP_MODEL


async def generate_structured(db: Session, prompt: str, output_model):
    """通用結構化生成，走例行降級鏈。回傳 (結果, 模型)。"""
    last_error: Exception | None = None
    for model, use_schema in ROUTINE_CHAIN:
        try:
            provider = GeminiProvider(model, db, use_schema=use_schema)
            return await provider.generate(prompt, output_model), model
        except (QuotaExceededError, UpstreamError) as exc:
            logger.warning("結構化生成 %s 失敗，降級下一層：%s", model, exc.message)
            last_error = exc
    raise UpstreamError("所有模型皆不可用") from last_error
