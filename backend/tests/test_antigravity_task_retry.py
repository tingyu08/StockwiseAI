"""interaction 讀不到時，必須「重建一個新任務」而不是重試同一個 id。

正式環境每一輪新聞研究約有一檔踩到：POST 建立成功（額度已扣、agent 已在跑），
但接下來對該 interaction 的所有 GET 都回 403 permission_denied——同一輪其他
interaction 全部正常 200。跨 21 秒重試同一個 id 完全無效，所以唯一有機會
救回該檔的手段是丟掉它、開一個新任務。
"""
import pytest
from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.core.rate_limiter import used_today
from app.models.analysis import AiUsageLog
from app.providers.ai import antigravity
from app.providers.ai.antigravity import AGENT_ID, AntigravityProvider


def _patch_tasks(monkeypatch, outcomes):
    """outcomes: 每次 _run_task 的結果（例外實例或要回傳的 interaction dict）。"""
    seq = list(outcomes)
    attempts = []

    async def fake_run_task(self, prompt):
        attempts.append(prompt)
        outcome = seq.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(AntigravityProvider, "_run_task", fake_run_task)
    return attempts


def _completed(text="近 7 天無重大新聞"):
    return {
        "id": "job-ok",
        "status": "completed",
        "steps": [{"type": "model_output",
                   "content": [{"type": "text", "text": text}]}],
    }


async def test_unreadable_interaction_triggers_a_brand_new_task(monkeypatch):
    db = SessionLocal()
    try:
        attempts = _patch_tasks(monkeypatch, [
            antigravity._InteractionUnreadable("讀不到（403，等待 21s）"),
            _completed("台積電法說會偏多"),
        ])

        text = await AntigravityProvider(db).research_news("2330", "台積電", "TW")

        assert text == "台積電法說會偏多"
        assert len(attempts) == 2          # 確實重跑了一次完整任務
        assert attempts[0] == attempts[1]  # 同一份 prompt
    finally:
        db.close()


async def test_gives_up_after_the_attempt_budget(monkeypatch):
    """重建也讀不到就放棄——不能無止盡開新任務把額度燒完。"""
    db = SessionLocal()
    try:
        attempts = _patch_tasks(monkeypatch, [
            antigravity._InteractionUnreadable("讀不到（403）")
            for _ in range(antigravity.TASK_ATTEMPTS)
        ])

        with pytest.raises(antigravity._InteractionUnreadable):
            await AntigravityProvider(db).research_news("2330", "台積電", "TW")

        assert len(attempts) == antigravity.TASK_ATTEMPTS
    finally:
        db.close()


async def test_other_upstream_errors_are_not_retried(monkeypatch):
    """只有「讀不到」才重建。逾時、空白結果等重跑一次也不會變好，
    重試只是多燒一次額度。"""
    db = SessionLocal()
    try:
        attempts = _patch_tasks(monkeypatch, [UpstreamError("任務逾時（>480s）")])

        with pytest.raises(UpstreamError):
            await AntigravityProvider(db).research_news("2330", "台積電", "TW")

        assert len(attempts) == 1
    finally:
        db.close()


async def test_empty_result_is_not_retried(monkeypatch):
    db = SessionLocal()
    try:
        attempts = _patch_tasks(monkeypatch, [{"id": "x", "status": "completed"}])

        with pytest.raises(UpstreamError) as exc:
            await AntigravityProvider(db).research_news("2330", "台積電", "TW")

        assert "空白" in exc.value.message
        assert len(attempts) == 1
    finally:
        db.close()


async def test_first_attempt_success_costs_one_task(monkeypatch):
    db = SessionLocal()
    try:
        attempts = _patch_tasks(monkeypatch, [_completed()])
        await AntigravityProvider(db).research_news("2330", "台積電", "TW")
        assert len(attempts) == 1
    finally:
        db.close()


async def test_retry_reserves_its_own_quota(monkeypatch):
    """重建任務必須各自預約額度——共用一筆會讓用量少記一次。

    這裡不 mock _run_task，改 mock 更底層的 _create/_wait，讓真正的
    額度流程跑過去。
    """
    db = SessionLocal()
    try:
        before = used_today(db, AGENT_ID)
        # 這個測試會真的寫入 ai_usage_log，而測試共用同一個 test.db，
        # 不清掉會讓後面斷言「用量為 0」的測試失敗
        high_water = db.execute(select(func.max(AiUsageLog.id))).scalar() or 0
        created = []

        async def fake_create(self, prompt):
            created.append(prompt)
            return {"id": f"job-{len(created)}", "status": "in_progress"}

        async def fake_wait(self, interaction):
            if interaction["id"] == "job-1":
                raise antigravity._InteractionUnreadable("讀不到（403）")
            return _completed()

        monkeypatch.setattr(AntigravityProvider, "_create", fake_create)
        monkeypatch.setattr(AntigravityProvider, "_wait", fake_wait)

        try:
            await AntigravityProvider(db).research_news("2330", "台積電", "TW")

            assert len(created) == 2
            # 兩個任務都真的送到上游 → 兩次都要計入用量
            assert used_today(db, AGENT_ID) == before + 2
        finally:
            db.execute(delete(AiUsageLog).where(AiUsageLog.id > high_water))
            db.commit()
    finally:
        db.close()
