import logging
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter

from fastapi import Request
from sqlalchemy import event

from app.core.db import engine

logger = logging.getLogger("app.performance")
SLOW_REQUEST_MS = 1000.0
LIVENESS_PATH = "/api/v1/health/live"


@dataclass
class TimingState:
    db_ms: float = 0.0
    db_queries: int = 0


_current_timing: ContextVar[TimingState | None] = ContextVar("request_timing", default=None)


def _before_cursor_execute(conn, _cursor, _statement, _parameters, _context, _many):
    conn.info.setdefault("stockwise_query_started", []).append(perf_counter())


def _record_query(conn) -> None:
    starts = conn.info.get("stockwise_query_started", [])
    if not starts:
        return
    elapsed_ms = (perf_counter() - starts.pop()) * 1000
    state = _current_timing.get()
    if state is not None:
        state.db_ms += elapsed_ms
        state.db_queries += 1


def _after_cursor_execute(conn, _cursor, _statement, _parameters, _context, _many):
    _record_query(conn)


def _handle_error(exception_context):
    if exception_context.connection is not None:
        _record_query(exception_context.connection)


def install_db_timing() -> None:
    if not event.contains(engine, "before_cursor_execute", _before_cursor_execute):
        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    if not event.contains(engine, "after_cursor_execute", _after_cursor_execute):
        event.listen(engine, "after_cursor_execute", _after_cursor_execute)
    if not event.contains(engine, "handle_error", _handle_error):
        event.listen(engine, "handle_error", _handle_error)


def _log_request(request: Request, status: int, total_ms: float, state: TimingState) -> None:
    if request.url.path == LIVENESS_PATH:
        return
    log = logger.warning if total_ms >= SLOW_REQUEST_MS else logger.info
    log(
        "method=%s path=%s status=%d total_ms=%.1f db_ms=%.1f db_queries=%d",
        request.method,
        request.url.path,
        status,
        total_ms,
        state.db_ms,
        state.db_queries,
    )


async def request_timing_middleware(request: Request, call_next):
    state = TimingState()
    token = _current_timing.set(state)
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        total_ms = (perf_counter() - started) * 1000
        _log_request(request, 500, total_ms, state)
        raise
    else:
        total_ms = (perf_counter() - started) * 1000
        response.headers["Server-Timing"] = (
            f"app;dur={total_ms:.1f}, db;dur={state.db_ms:.1f}"
        )
        _log_request(request, response.status_code, total_ms, state)
        return response
    finally:
        _current_timing.reset(token)
