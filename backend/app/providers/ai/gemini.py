"""Gemini API Provider（REST），同時服務 Gemini 與 Gemma 模型。

- Gemini 模型：response_schema 強制 JSON
- Gemma 模型：response_mime_type=json ＋ prompt 內嵌 schema（Gemma 不吃 response_schema）
- 所有輸出過 Pydantic 驗證，失敗重試一次
- 每次呼叫寫入 ai_usage_log（額度計數的資料來源）
"""
import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.core.rate_limiter import ensure_quota
from app.models.analysis import AiUsageLog
from app.providers.ai.base import AIProvider, AnalysisContext
from app.providers.ai.schemas import AnalysisReport, BatchAnalysisResult

logger = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是一位嚴謹的量化股票分析師。根據提供的技術面、籌碼面與新聞面資料產出分析。
規則：
- 只根據提供的資料判斷，不虛構資訊
- confidence 反映訊號一致性：多指標同向才給高值，訊號矛盾給低值
- 三情境（bull/base/bear）的 target_price 必須合理圍繞現價，probability 總和約為 1
- reasoning 用繁體中文、150 字以內，聚焦關鍵訊號
- risks 列 2~4 條具體風險
此為模擬研究用途，非投資建議。"""

T = TypeVar("T", bound=BaseModel)


class GeminiProvider(AIProvider):
    provider_name = "gemini"

    def __init__(self, model: str, db: Session, use_schema: bool = True):
        self.model_name = model
        self.db = db
        self.use_schema = use_schema  # Gemma 模型設 False

    async def analyze_batch(self, contexts: list[AnalysisContext]) -> BatchAnalysisResult:
        ensure_quota(self.db, self.model_name)
        prompt = self._batch_prompt(contexts)
        return await self._generate(prompt, BatchAnalysisResult)

    async def generate(self, prompt: str, output_model: type[T]) -> T:
        """通用結構化生成（總評等非單股任務用）。含額度檢查與用量記錄。"""
        ensure_quota(self.db, self.model_name)
        return await self._generate(prompt, output_model)

    async def analyze_deep(self, context: AnalysisContext) -> AnalysisReport:
        ensure_quota(self.db, self.model_name)
        prompt = (
            "請對以下這一檔股票做深度分析，特別注意各指標間的背離與量價關係。\n\n"
            + self._context_block(context)
        )
        return await self._generate(prompt, AnalysisReport)

    # ---- internals ----

    def _batch_prompt(self, contexts: list[AnalysisContext]) -> str:
        blocks = "\n\n---\n\n".join(self._context_block(c) for c in contexts)
        symbols = "、".join(c.symbol for c in contexts)
        return (
            f"請分析以下 {len(contexts)} 檔股票，於 reports 陣列中依序回傳每一檔的報告。\n"
            f"注意：symbol 欄位只填股票代號本身（{symbols}），不要加市場前綴或名稱。\n\n{blocks}"
        )

    @staticmethod
    def _context_block(c: AnalysisContext) -> str:
        parts = [f"【{c.market}/{c.symbol}】", c.price_summary]
        if c.flow_summary:
            parts.append(f"籌碼面：{c.flow_summary}")
        if c.premium_summary:
            parts.append(f"折溢價：{c.premium_summary}")
        if c.news_summary:
            parts.append(f"新聞面：{c.news_summary}")
        return "\n".join(parts)

    async def _generate(self, prompt: str, output_model: type[T]) -> T:
        last_error: Exception | None = None
        for attempt in range(2):  # 驗證失敗重試一次
            raw = await self._call_api(prompt, output_model)
            try:
                return output_model.model_validate_json(raw)
            except ValidationError as exc:
                logger.warning("%s 輸出驗證失敗（第 %d 次）: %s", self.model_name, attempt + 1, exc)
                last_error = exc
        raise UpstreamError(f"{self.model_name} 連續輸出無效 JSON") from last_error

    async def _call_api(self, prompt: str, output_model: type[BaseModel]) -> str:
        settings = get_settings()
        generation_config: dict = {"responseMimeType": "application/json"}
        if self.use_schema:
            generation_config["responseSchema"] = _to_gemini_schema(
                output_model.model_json_schema()
            )
            full_prompt = prompt
        else:
            # Gemma：schema 嵌入 prompt
            full_prompt = (
                f"{prompt}\n\n請嚴格以符合以下 JSON Schema 的 JSON 回覆，不要有其他文字：\n"
                f"{json.dumps(output_model.model_json_schema(), ensure_ascii=False)}"
            )

        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
            "generationConfig": generation_config,
        }
        if self.use_schema:
            body["systemInstruction"] = {"parts": [{"text": SYSTEM_PROMPT}]}
        else:
            body["contents"][0]["parts"][0]["text"] = (
                SYSTEM_PROMPT + "\n\n" + full_prompt
            )

        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.post(
                f"{BASE_URL}/{self.model_name}:generateContent",
                params={"key": settings.gemini_api_key},
                json=body,
            )
        if res.status_code == 429:
            raise UpstreamError(f"{self.model_name} 被 Google 端限流（429）")
        if res.status_code != 200:
            logger.error("Gemini API %s: %s", res.status_code, res.text[:500])
            raise UpstreamError(f"{self.model_name} API 錯誤（{res.status_code}）")

        data = res.json()
        self._log_usage(data.get("usageMetadata", {}))
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise UpstreamError(f"{self.model_name} 回應結構異常") from exc

    def _log_usage(self, usage: dict) -> None:
        self.db.add(
            AiUsageLog(
                provider=self.provider_name,
                model=self.model_name,
                input_tokens=usage.get("promptTokenCount"),
                output_tokens=usage.get("candidatesTokenCount"),
            )
        )
        self.db.commit()


def _to_gemini_schema(schema: dict) -> dict:
    """Pydantic JSON Schema → Gemini responseSchema（展開 $ref、拿掉不支援欄位）。"""
    defs = schema.get("$defs", {})

    def resolve(node: dict) -> dict:
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            return resolve(defs[name])
        out: dict = {}
        node_type = node.get("type")
        if "anyOf" in node:  # Optional[...] → 取第一個非 null 型別
            non_null = [n for n in node["anyOf"] if n.get("type") != "null"]
            return resolve(non_null[0]) if non_null else {"type": "STRING"}
        if node_type == "object":
            out["type"] = "OBJECT"
            out["properties"] = {k: resolve(v) for k, v in node.get("properties", {}).items()}
            if node.get("required"):
                out["required"] = node["required"]
        elif node_type == "array":
            out["type"] = "ARRAY"
            out["items"] = resolve(node.get("items", {}))
        elif node_type == "string":
            out["type"] = "STRING"
            if "enum" in node:
                out["enum"] = node["enum"]
        elif node_type == "number":
            out["type"] = "NUMBER"
        elif node_type == "integer":
            out["type"] = "INTEGER"
        elif node_type == "boolean":
            out["type"] = "BOOLEAN"
        else:
            out["type"] = "STRING"
        return out

    return resolve(schema)
