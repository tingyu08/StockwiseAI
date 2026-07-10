"""Single-user bearer-token protection for the private API."""

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.envelope import fail

PUBLIC_PATHS = frozenset({"/api/v1/health"})


async def require_api_token(request: Request, call_next):
    settings = get_settings()
    path = request.url.path
    is_job_trigger = (
        request.method == "POST"
        and path.startswith("/api/v1/jobs/")
        and path.endswith(":run")
        and not path.startswith("/api/v1/jobs/runs/")
    )
    is_public = (
        request.method == "OPTIONS"
        or path in PUBLIC_PATHS
        or is_job_trigger
        or not path.startswith("/api/v1/")
    )
    if settings.api_token and not is_public:
        scheme, _, credential = request.headers.get("Authorization", "").partition(" ")
        valid = scheme.lower() == "bearer" and hmac.compare_digest(
            credential, settings.api_token
        )
        if not valid:
            return JSONResponse(
                status_code=401,
                content=fail("需要有效的 API Token").model_dump(),
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)
