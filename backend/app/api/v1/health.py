from fastapi import APIRouter

from app.core.envelope import Envelope, ok

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Envelope)
async def health() -> Envelope:
    return ok({"status": "ok"})
