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

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.envelope import fail
from app.models import UserSession

SESSION_COOKIE = "stockwise_session"
CSRF_COOKIE = "stockwise_csrf"
SESSION_MAX_AGE = 30 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_MAX_ATTEMPTS = 5
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
        attempts = _attempts[client_id]
        while attempts and attempts[0] <= current - LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        return 0 if len(attempts) < LOGIN_MAX_ATTEMPTS else max(
            1, int(LOGIN_WINDOW_SECONDS - (current - attempts[0]))
        )


def record_failed_login(client_id: str) -> None:
    with _attempts_lock:
        _attempts[client_id].append(time.monotonic())


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

    session = get_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return JSONResponse(status_code=401, content=fail("請先登入").model_dump())
    request.state.user_id = session.user_id
    if request.method not in SAFE_METHODS:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("X-CSRF-Token", "")
        if not cookie or not hmac.compare_digest(cookie, header) or not hmac.compare_digest(hash_token(cookie), session.csrf_hash):
            return JSONResponse(status_code=403, content=fail("安全驗證失敗").model_dump())
    return await call_next(request)
