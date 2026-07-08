"""Unified API response envelope: {success, data, error, meta}."""
from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    total: int | None = None
    page: int | None = None
    limit: int | None = None


class Envelope(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    meta: Meta | None = None


def ok(data: Any = None, meta: Meta | None = None) -> Envelope:
    return Envelope(success=True, data=data, meta=meta)


def fail(error: str) -> Envelope:
    return Envelope(success=False, error=error)
