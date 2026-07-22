"""AI 降級鏈 Router。

例行批次（analyze_batch / generate_structured）：僅使用 flash-lite，不設備援
重要任務（交易決策、每日簡報）：3.6-flash 優先，額度不足自動降級至 flash-lite
"""
import logging

from sqlalchemy.orm import Session

from app.core.exceptions import QuotaExceededError, UpstreamError
from app.providers.ai.base import AnalysisContext
from app.providers.ai.gemini import GeminiProvider
from app.providers.ai.schemas import BatchAnalysisResult

logger = logging.getLogger(__name__)

ROUTINE_CHAIN = ["gemini-3.5-flash-lite"]
# 品質優先的模型，供交易決策與每日簡報使用（單檔深度分析功能已移除，
# 故不再叫 DEEP_MODEL）
PREMIUM_MODEL = "gemini-3.6-flash"
PREMIUM_CHAIN = [PREMIUM_MODEL, *ROUTINE_CHAIN]


def validate_configured_models() -> None:
    """啟動時確認降級鏈用到的模型都在 quotas.yaml 裡。

    少了設定的話，ensure_quota 會丟「未設定 X 的額度」的
    QuotaExceededError——看起來跟「今日額度用盡」一模一樣，
    模型名稱打錯字會被偽裝成正常的限流，很難查。
    """
    from app.core.config import get_settings

    configured = set(get_settings().load_quotas())
    referenced = {*ROUTINE_CHAIN, *PREMIUM_CHAIN}
    missing = sorted(referenced - configured)
    if missing:
        raise ValueError(
            f"quotas.yaml 缺少模型設定：{', '.join(missing)}"
            f"（已設定：{', '.join(sorted(configured))}）"
        )


def _next_step(chain: list[str], index: int) -> str:
    return "falling back to next model" if index + 1 < len(chain) else "no models remaining"


async def analyze_batch(db: Session, contexts: list[AnalysisContext]) -> tuple[BatchAnalysisResult, str]:
    """回傳 (結果, 實際使用的模型)。"""
    last_error: Exception | None = None
    for index, model in enumerate(ROUTINE_CHAIN):
        try:
            provider = GeminiProvider(model, db)
            result = await provider.analyze_batch(contexts)
            return result, model
        except (QuotaExceededError, UpstreamError) as exc:
            logger.warning(
                "AI provider failed model=%s operation=batch error_type=%s error=%s; %s",
                model,
                type(exc).__name__,
                exc.message,
                _next_step(ROUTINE_CHAIN, index),
            )
            last_error = exc
    raise UpstreamError("所有例行分析模型皆不可用") from last_error


async def analyze_trading_batch(
    db: Session, contexts: list[AnalysisContext]
) -> tuple[BatchAnalysisResult, str]:
    """交易決策分析優先使用 3.5，失敗或額度不足時自動降級。"""
    last_error: Exception | None = None
    for index, model in enumerate(PREMIUM_CHAIN):
        try:
            provider = GeminiProvider(model, db)
            result = await provider.analyze_batch(contexts)
            return result, model
        except (QuotaExceededError, UpstreamError) as exc:
            logger.warning(
                "AI provider failed model=%s operation=trading_batch error_type=%s "
                "error=%s; %s",
                model,
                type(exc).__name__,
                exc.message,
                _next_step(PREMIUM_CHAIN, index),
            )
            last_error = exc
    raise UpstreamError("所有交易分析模型皆不可用") from last_error


async def generate_structured(db: Session, prompt: str, output_model):
    """通用結構化生成，走例行降級鏈。回傳 (結果, 模型)。"""
    last_error: Exception | None = None
    for index, model in enumerate(ROUTINE_CHAIN):
        try:
            provider = GeminiProvider(model, db)
            return await provider.generate(prompt, output_model), model
        except (QuotaExceededError, UpstreamError) as exc:
            logger.warning(
                "AI provider failed model=%s operation=structured error_type=%s error=%s; %s",
                model,
                type(exc).__name__,
                exc.message,
                _next_step(ROUTINE_CHAIN, index),
            )
            last_error = exc
    raise UpstreamError("所有模型皆不可用") from last_error


async def generate_premium_structured(db: Session, prompt: str, output_model):
    """重要摘要優先使用 3.5，失敗或額度不足時自動降級。"""
    last_error: Exception | None = None
    for index, model in enumerate(PREMIUM_CHAIN):
        try:
            provider = GeminiProvider(model, db)
            return await provider.generate(prompt, output_model), model
        except (QuotaExceededError, UpstreamError) as exc:
            logger.warning(
                "AI provider failed model=%s operation=premium_structured error_type=%s "
                "error=%s; %s",
                model,
                type(exc).__name__,
                exc.message,
                _next_step(PREMIUM_CHAIN, index),
            )
            last_error = exc
    raise UpstreamError("所有摘要模型皆不可用") from last_error
