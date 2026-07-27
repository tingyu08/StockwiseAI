"""Database-backed owner authentication and CSRF enforcement."""

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.envelope import fail
from app.models import UserSession

SESSION_COOKIE = "stockwise_session"
CSRF_COOKIE = "stockwise_csrf"
SESSION_MAX_AGE = 30 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_MAX_ATTEMPTS = 5
# 來源數超過這個量就順手掃掉過期項目（見 record_failed_login）
ATTEMPTS_SWEEP_THRESHOLD = 1024
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_PATHS = frozenset({
    "/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready",
    "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/session",
})

_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempts_lock = threading.Lock()


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def get_session(raw_token: str | None) -> UserSession | None:
    if not raw_token:
        return None
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw_token)))
        if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
            return None
        db.expunge(session)
        return session


def login_retry_after(client_id: str, now: float | None = None) -> int:
    current = now or time.monotonic()
    with _attempts_lock:
        # 用 get 而非索引：defaultdict 的索引讀取本身就會建出空 deque，
        # 等於每個「查詢過」的來源都留下一筆項目
        attempts = _attempts.get(client_id)
        if attempts is None:
            return 0
        while attempts and attempts[0] <= current - LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if not attempts:
            del _attempts[client_id]  # 視窗內已無紀錄，不留空殼
            return 0
        return 0 if len(attempts) < LOGIN_MAX_ATTEMPTS else max(
            1, int(LOGIN_WINDOW_SECONDS - (current - attempts[0]))
        )


def record_failed_login(client_id: str, now: float | None = None) -> None:
    current = now or time.monotonic()
    with _attempts_lock:
        _attempts[client_id].append(current)
        # 失敗後就再也不回來的來源（輪流換 IP 的掃描）沒有人會替它呼叫
        # login_retry_after 收拾，故在表變大時順手清掉過期項目，讓容量
        # 收斂於「視窗內真正活躍的來源數」而非歷史累計來源數
        if len(_attempts) > ATTEMPTS_SWEEP_THRESHOLD:
            _sweep_expired_attempts(current)


def _sweep_expired_attempts(now: float) -> None:
    """清掉整段視窗內都沒有新失敗的來源。呼叫端須持有 _attempts_lock。"""
    cutoff = now - LOGIN_WINDOW_SECONDS
    stale = [
        client_id
        for client_id, attempts in _attempts.items()
        if not attempts or attempts[-1] <= cutoff  # 連最新一筆都過期＝整筆可丟
    ]
    for client_id in stale:
        del _attempts[client_id]


def clear_failed_logins(client_id: str) -> None:
    with _attempts_lock:
        _attempts.pop(client_id, None)


def client_identifier(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _valid_job_token(request: Request) -> bool:
    expected = get_settings().job_token
    return bool(expected) and hmac.compare_digest(request.headers.get("X-Job-Token", ""), expected)


async def require_login(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS:
        return await call_next(request)
    is_job_trigger = request.method == "POST" and path.startswith("/api/v1/jobs/") and path.endswith(":run") and not path.startswith("/api/v1/jobs/runs/")
    is_job_status = request.method == "GET" and path.startswith("/api/v1/jobs/runs/")
    if (is_job_trigger or is_job_status) and _valid_job_token(request):
        return await call_next(request)

    # 每個非公開請求都要查一次 session。middleware 必須是 async（Starlette
    # 規定），但 get_session 是同步 DB 查詢——直接呼叫等於在 event loop 上
    # 阻塞，而且是全站每個請求都會踩到的固定成本，故丟到 threadpool 執行。
    session = await run_in_threadpool(get_session, request.cookies.get(SESSION_COOKIE))
    if session is None:
        return JSONResponse(status_code=401, content=fail("請先登入").model_dump())
    request.state.user_id = session.user_id
    if request.method not in SAFE_METHODS:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("X-CSRF-Token", "")
        if not cookie or not hmac.compare_digest(cookie, header) or not hmac.compare_digest(hash_token(cookie), session.csrf_hash):
            return JSONResponse(status_code=403, content=fail("安全驗證失敗").model_dump())
    return await call_next(request)
