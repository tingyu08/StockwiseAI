"""Antigravity 輪詢的 403/404 處理。

正式環境出現過：任務 POST 成功（額度已扣、agent 已在跑），下一個輪詢 GET
卻回 403 permission_denied，同一輪其他輪詢皆為 200 —— 屬偶發。
舊版把任何 4xx 當致命，等於為了一次偶發錯誤丟掉已付出的額度與數分鐘等待。
"""
import httpx  # noqa: F401  # antigravity 模組層級 monkeypatch 目標
import pytest

from app.core.db import SessionLocal
from app.core.exceptions import UpstreamError
from app.providers.ai import antigravity
from app.providers.ai.antigravity import API_REVISION, AntigravityProvider


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _client_factory(monkeypatch, responses, captured_headers=None):
    seq = list(responses)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            if captured_headers is not None:
                captured_headers.append(kwargs.get("headers", {}))
            return seq.pop(0)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(antigravity.httpx, "AsyncClient", lambda **kw: Client())
    monkeypatch.setattr(antigravity.asyncio, "sleep", no_sleep)


DONE = {"id": "job-1", "status": "completed"}


async def test_transient_403_is_retried_not_fatal(monkeypatch):
    """偶發 403 之後應該繼續輪詢並拿到結果，而不是整檔放棄。"""
    _client_factory(monkeypatch, [
        _Resp(403, text='{"error":{"code":"permission_denied"}}'),
        _Resp(200, DONE),
    ])
    db = SessionLocal()
    try:
        result = await AntigravityProvider(db)._wait({"id": "job-1", "status": "in_progress"})
    finally:
        db.close()
    assert result["status"] == "completed"


async def test_persistent_403_still_fails(monkeypatch):
    """真的沒權限時不能無止盡重試，超過上限就放棄。"""
    _client_factory(monkeypatch, [
        _Resp(403, text="denied") for _ in range(antigravity.POLL_FORBIDDEN_RETRIES + 1)
    ])
    db = SessionLocal()
    try:
        with pytest.raises(UpstreamError) as exc:
            await AntigravityProvider(db)._wait({"id": "job-1", "status": "in_progress"})
    finally:
        db.close()
    assert "403" in exc.value.message


async def test_poll_sends_api_revision_header(monkeypatch):
    """建立任務帶了 Api-Revision（background 執行需要），
    讀回同一個 background 任務也必須帶，否則語意不一致。"""
    headers = []
    _client_factory(monkeypatch, [_Resp(200, DONE)], captured_headers=headers)
    db = SessionLocal()
    try:
        await AntigravityProvider(db)._wait({"id": "job-1", "status": "in_progress"})
    finally:
        db.close()
    assert headers and headers[0].get("Api-Revision") == API_REVISION


async def test_other_4xx_remains_fatal(monkeypatch):
    """400/401 這類不是暫時性的，維持立即失敗（不浪費 8 分鐘等待）。"""
    _client_factory(monkeypatch, [_Resp(401, text="bad key")])
    db = SessionLocal()
    try:
        with pytest.raises(UpstreamError) as exc:
            await AntigravityProvider(db)._wait({"id": "job-1", "status": "in_progress"})
    finally:
        db.close()
    assert "401" in exc.value.message


async def test_5xx_still_retries(monkeypatch):
    """既有行為不得被破壞：5xx 仍視為暫時性。"""
    _client_factory(monkeypatch, [_Resp(503, text="upstream"), _Resp(200, DONE)])
    db = SessionLocal()
    try:
        result = await AntigravityProvider(db)._wait({"id": "job-1", "status": "in_progress"})
    finally:
        db.close()
    assert result["status"] == "completed"
