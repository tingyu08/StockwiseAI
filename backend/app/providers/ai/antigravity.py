"""Antigravity 託管 Agent（Interactions API）— 新聞面研究專用。

定位（docs/PLAN.md §4.0）：
- 自帶 Google 搜尋＋URL 抓取的託管 agent，自主上網查個股新聞，免串新聞 API
- 不支援 structured output / temperature 等參數（preview 限制），
  輸出為自由文字摘要，不驅動自動下單——只作為主分析管線的 news_summary 輸入
- 每次任務 token 消耗遠大於單次 generateContent，一律寫入 ai_usage_log
"""
import asyncio
import logging
from time import monotonic

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import QuotaExceededError, UpstreamError
from app.core.rate_limiter import cancel_quota, finalize_quota, reserve_quota

logger = logging.getLogger(__name__)

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
AGENT_ID = "antigravity-preview-05-2026"
API_REVISION = "2026-05-20"  # background 執行需要

POLL_INTERVAL_SEC = 5
MAX_WAIT_SEC = 480  # agent 任務通常 1~3 分鐘，8 分鐘仍未完成視為失敗
# agent 任務會自己上網搜尋與抓網頁，token 量遠大於一次 generateContent。
# 實測單檔新聞研究約 34K tokens（其中 input 約 31K 多為 grounded search 內容）。
# 預約時若估 0，TPM 防線對 in-flight 任務等於完全失效（額度只有 100K）。
ESTIMATED_TOKENS_PER_TASK = 35_000

NEWS_PROMPT_TEMPLATE = """你是一位財經新聞研究員。請搜尋「{name}（{market_label}股票代號 {symbol}）」最近 7 天的新聞與重大事件。

搜尋方向：財報/營收公告、重大合約或訂單、產業政策與供應鏈動態、分析師評等變動、經營層或股權變動、法說會訊息。

輸出要求（繁體中文純文字，不要 Markdown 標題）：
1. 第一行：一句話總結近期新聞面基調（偏多／偏空／中性，與原因）
2. 接著列出 2~5 條重要事件，每條一行，格式「MM/DD 事件摘要（來源媒體｜來源 URL）」
3. 只寫有明確來源的事實，不要推測與投資建議；找不到重要新聞就寫「近 7 天無重大新聞」
4. 全文控制在 600 字以內，URL 不計入字數"""

_MARKET_LABELS = {"TW": "台灣", "US": "美國"}


class _RequestNotSent(UpstreamError):
    """任務未送達上游（連線層失敗）——呼叫端應釋放額度預約而非計為用量。"""


class AntigravityProvider:
    provider_name = "antigravity"
    model_name = AGENT_ID

    def __init__(self, db: Session):
        self.db = db

    async def research_news(self, symbol: str, name: str, market: str) -> str:
        """搜尋個股近期新聞，回傳純文字摘要。額度不足丟 QuotaExceededError。"""
        reservation_id: int | None = reserve_quota(
            self.db, self.model_name, estimated_tokens=ESTIMATED_TOKENS_PER_TASK
        )
        prompt = NEWS_PROMPT_TEMPLATE.format(
            name=name, symbol=symbol, market_label=_MARKET_LABELS.get(market, market)
        )

        def settle(**usage_kwargs) -> None:
            nonlocal reservation_id
            if reservation_id is not None:
                rid, reservation_id = reservation_id, None
                finalize_quota(self.db, rid, provider=self.provider_name, **usage_kwargs)

        def release() -> None:
            nonlocal reservation_id
            if reservation_id is not None:
                rid, reservation_id = reservation_id, None
                cancel_quota(self.db, rid)

        try:
            try:
                interaction = await self._create(prompt)
            except _RequestNotSent:
                # 任務根本沒建立成功，Google 端不會計數 → 不該扣我們的 RPD
                release()
                raise
            except Exception:
                settle()
                raise
            try:
                interaction = await self._wait(interaction)
            except Exception:
                settle()  # 任務已建立，額度已被上游計入
                raise
            usage = interaction.get("usage") or {}
            settle(
                input_tokens=_to_int(usage.get("total_input_tokens")),
                output_tokens=_to_int(usage.get("total_output_tokens")),
            )
        finally:
            # 兜底：asyncio.CancelledError 繼承 BaseException，上面的
            # except Exception 攔不到，沒有這層預約會永遠留著佔用額度
            release()

        text = _extract_output_text(interaction)
        if not text:
            raise UpstreamError(f"Antigravity 對 {symbol} 的新聞研究回傳空白結果")
        return text

    # ---- internals ----

    async def _create(self, prompt: str) -> dict:
        settings = get_settings()
        body = {
            "agent": AGENT_ID,
            "input": prompt,
            "environment": "remote",
            # 只給搜尋與抓網頁——新聞研究不需要沙箱跑程式，省 token 也降低意外行為
            "tools": [{"type": "google_search"}, {"type": "url_context"}],
            "background": True,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                res = await client.post(
                    INTERACTIONS_URL,
                    headers={
                        "x-goog-api-key": settings.gemini_api_key,
                        "Api-Revision": API_REVISION,
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise UpstreamError("Antigravity 建立新聞任務逾時") from exc
        except httpx.ConnectError as exc:
            # 連線都沒建立＝任務未送出，額度不該被扣（由呼叫端 release）
            raise _RequestNotSent("Antigravity 建立新聞任務連線失敗") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("Antigravity 建立新聞任務連線失敗") from exc
        if res.status_code == 429:
            # 用 QuotaExceededError（HTTP 429）而非 UpstreamError：
            # news_research_daily 只對 QuotaExceededError 提前收工，
            # 否則會繼續逐檔轟炸一個已經在限流的 API。
            raise QuotaExceededError("Antigravity 被 Google 端限流（429）")
        if res.status_code != 200:
            logger.error("Antigravity create %s: %s", res.status_code, res.text[:500])
            raise UpstreamError(f"Antigravity API 錯誤（{res.status_code}）")
        return res.json()

    async def _wait(self, interaction: dict) -> dict:
        """輪詢 background interaction 直到完成。GET 不計 RPD 額度。"""
        settings = get_settings()
        interaction_id = interaction.get("id")
        if not interaction_id:
            raise UpstreamError("Antigravity 回應缺少 interaction id")

        deadline = monotonic() + MAX_WAIT_SEC
        async with httpx.AsyncClient(timeout=30) as client:
            while interaction.get("status") in ("in_progress", None, "queued"):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise UpstreamError(f"Antigravity 任務逾時（>{MAX_WAIT_SEC}s）")
                await asyncio.sleep(min(POLL_INTERVAL_SEC, remaining))
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise UpstreamError(f"Antigravity 任務逾時（>{MAX_WAIT_SEC}s）")
                try:
                    res = await client.get(
                        f"{INTERACTIONS_URL}/{interaction_id}",
                        headers={"x-goog-api-key": settings.gemini_api_key},
                        timeout=min(30, remaining),
                    )
                except httpx.TimeoutException:
                    logger.warning(
                        "Antigravity poll timed out; retrying interaction %s",
                        interaction_id,
                    )
                    continue
                except httpx.HTTPError as exc:
                    # 任務此刻仍在 Google 那邊跑，額度也早就扣了。為了一次
                    # 傳輸瞬斷就放棄，等於白白丟掉已付出的額度與數分鐘等待；
                    # 只要還沒到 deadline 就繼續輪詢。
                    logger.warning(
                        "Antigravity poll transport error (%s); retrying interaction %s",
                        type(exc).__name__,
                        interaction_id,
                    )
                    continue
                if res.status_code >= 500:
                    logger.warning(
                        "Antigravity poll %s (transient); retrying interaction %s",
                        res.status_code,
                        interaction_id,
                    )
                    continue
                if res.status_code != 200:
                    # 4xx 才是真的沒救（任務不存在、金鑰無效等）
                    logger.error("Antigravity poll %s: %s", res.status_code, res.text[:300])
                    raise UpstreamError(f"Antigravity 輪詢失敗（{res.status_code}）")
                interaction = res.json()

        if interaction.get("status") != "completed":
            logger.error("Antigravity 任務未完成: %s", str(interaction)[:500])
            raise UpstreamError(f"Antigravity 任務狀態異常（{interaction.get('status')}）")
        return interaction

def _extract_output_text(interaction: dict) -> str:
    """實測 background interaction 的 GET 回應沒有頂層 output_text，
    最終回覆在 steps 最後一個 type='model_output' 的 content[].text。"""
    if text := (interaction.get("output_text") or "").strip():
        return text
    for step in reversed(interaction.get("steps", [])):
        if step.get("type") == "model_output":
            parts = [
                c.get("text", "") for c in step.get("content", []) if c.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p).strip()
    return ""


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
