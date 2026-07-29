"""Database-backed owner authentication and CSRF enforcement."""

import hashlib
import hmac
import ipaddress
import logging
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

logger = logging.getLogger(__name__)

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


_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
    )
)


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = get_settings().trusted_proxy_ips.strip()
    if not raw:
        return ()
    if raw == "private":
        return _PRIVATE_NETWORKS
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # 設定錯字不該讓整個服務起不來，但必須留下紀錄——
            # 靜默忽略會讓限流悄悄退回「全站同一個桶」
            logger.warning("TRUSTED_PROXY_IPS 有無法解析的項目，已略過：%s", item)
    return tuple(networks)


def _is_trusted_proxy(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _trusted_proxy_networks())


def _normalise_forwarded_host(value: str) -> str:
    """XFF 項目可能帶埠或是 IPv6 括號形式，取出純位址。"""
    value = value.strip()
    if value.startswith("["):  # [::1]:8080
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:  # 1.2.3.4:5678（IPv6 會有多個冒號，不可截斷）
        return value.split(":", 1)[0]
    return value


def client_identifier(request: Request) -> str:
    """登入限流的來源識別。

    直連時 peer 就是來源。但位於反向代理後方時，peer 永遠是代理——
    正式環境的 log 每一行都是 Zeabur ingress 的 10.42.0.1，等於所有訪客
    共用同一個限流桶：任何人連打 5 次錯誤密碼就能把唯一的擁有者鎖在門外，
    每 5 分鐘重複一次即可無限期封鎖，且完全不需要任何憑證。

    故 peer 屬於信任代理時改採 X-Forwarded-For，並且**由右往左**取第一個
    非信任項。方向很重要：右端是信任代理實際觀察到的來源，左端則完全由
    用戶端自填。取最左（uvicorn 的 --forwarded-allow-ips="*" 就是這個行為）
    會讓攻擊者每次送不同的假 IP，限流直接失效——比原本的問題更糟。
    """
    peer = request.client.host if request.client else ""
    if not peer:
        return "unknown"
    if not _is_trusted_proxy(peer):
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "")
    for candidate in reversed(forwarded.split(",")):
        host = _normalise_forwarded_host(candidate)
        if host and not _is_trusted_proxy(host):
            return host
    return peer  # 整條鏈都是信任代理（或沒有 XFF）：退回 peer，不會 fail-open


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
