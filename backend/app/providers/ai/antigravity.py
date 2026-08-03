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
# 輪詢 403 permission_denied / 404 的重試次數。
#
# 實測修正：這**不是**偶發的單次錯誤。正式環境的紀錄顯示同一個 interaction
# 會連續 4 次、跨 21 秒全部回 403，而同一輪其他 interaction 全部正常 200。
# 也就是「某些 interaction 建立後就再也讀不到」，重試同一個 id 沒有意義。
# 故這裡只留少量重試處理真正的瞬斷，之後改由呼叫端「重建一個新任務」
# （見 research_news 的 TASK_ATTEMPTS）——那才是真正能救回該檔的手段。
POLL_FORBIDDEN_RETRIES = 2
# 整個任務的嘗試次數（每次都是一個全新的 interaction）。
# 重建要多付一次 RPD，但 Antigravity 的 rpd=100 而每日實際只用約 20，
# 拿一次額度換回一檔新聞研究非常划算。
TASK_ATTEMPTS = 2
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


class InteractionUnreadableError(UpstreamError):
    """任務已建立，但它的 interaction 持續讀不到（403 permission_denied）。

    與一般 UpstreamError 分開，是因為處置方式不同：重試同一個 id 無效。
    隔離發生時重建一個新任務還有機會救回；但**連續**發生就是上游異常，
    呼叫端必須整輪收工而不是逐檔重建（見 jobs.NEWS_UNREADABLE_STREAK_LIMIT）。
    """


class AntigravityProvider:
    provider_name = "antigravity"
    model_name = AGENT_ID

    def __init__(self, db: Session):
        self.db = db

    async def research_news(self, symbol: str, name: str, market: str) -> str:
        """搜尋個股近期新聞，回傳純文字摘要。額度不足丟 QuotaExceededError。

        interaction 讀不到時會重建一個新任務再試（見 TASK_ATTEMPTS）——
        正式環境約每輪有一檔會踩到，重試同一個 id 無效。
        """
        prompt = NEWS_PROMPT_TEMPLATE.format(
            name=name, symbol=symbol, market_label=_MARKET_LABELS.get(market, market)
        )
        for attempt in range(1, TASK_ATTEMPTS + 1):
            try:
                interaction = await self._run_task(prompt)
            except InteractionUnreadableError as exc:
                if attempt >= TASK_ATTEMPTS:
                    raise
                logger.warning(
                    "Antigravity %s 的 interaction 讀不到（%s），改建立新任務重試（第 %d 次）",
                    symbol, exc.message, attempt,
                )
                continue
            text = _extract_output_text(interaction)
            if not text:
                raise UpstreamError(f"Antigravity 對 {symbol} 的新聞研究回傳空白結果")
            return text
        raise UpstreamError(f"Antigravity 對 {symbol} 的新聞研究未能完成")  # 不會走到

    async def _run_task(self, prompt: str) -> dict:
        """跑一次完整任務（預約額度 → 建立 interaction → 輪詢至完成）。"""
        reservation_id: int | None = reserve_quota(
            self.db, self.model_name, estimated_tokens=ESTIMATED_TOKENS_PER_TASK
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
        return interaction

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
            # scope=upstream：Google 不告訴我們是日額度還是分鐘級限流，
            # 無從分辨就保守收工（我方的分鐘級限流由本地 rate_limiter 標成 rpm/tpm）。
            raise QuotaExceededError(
                "Antigravity 被 Google 端限流（429）", scope="upstream"
            )
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
        forbidden_seen = 0
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
                        headers={
                            "x-goog-api-key": settings.gemini_api_key,
                            # 建立任務時帶了 Api-Revision（background 執行需要），
                            # 讀回同一個 background 任務卻不帶，是不一致；
                            # 正式環境出現過偶發的 403 permission_denied。
                            "Api-Revision": API_REVISION,
                        },
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
                if res.status_code in (403, 404):
                    forbidden_seen += 1
                    if forbidden_seen <= POLL_FORBIDDEN_RETRIES:
                        logger.warning(
                            "Antigravity poll %s（第 %d/%d 次，重試）：%s",
                            res.status_code, forbidden_seen, POLL_FORBIDDEN_RETRIES,
                            res.text[:200],
                        )
                        continue
                    # 同一個 id 連續讀不到＝這個 interaction 壞了，再等也沒用。
                    # 記下實際等待秒數：若日後 log 顯示等更久會恢復，就該調整
                    # 策略而非重建任務（目前的證據是跨 20 秒以上仍全數 403）。
                    waited = int(MAX_WAIT_SEC - (deadline - monotonic()))
                    logger.error(
                        "Antigravity interaction %s 持續回 %s（%d 次、共等 %d 秒），"
                        "視為不可讀取，交由呼叫端重建任務",
                        interaction_id, res.status_code, forbidden_seen, waited,
                    )
                    raise InteractionUnreadableError(
                        f"Antigravity interaction 讀不到（{res.status_code}，等待 {waited}s）"
                    )
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
