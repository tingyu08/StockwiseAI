"""First-owner registration and database-backed login endpoints."""

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth import (CSRF_COOKIE, SESSION_COOKIE, SESSION_MAX_AGE, clear_failed_logins,
                           client_identifier, get_session, hash_token, login_retry_after,
                           record_failed_login)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.envelope import Envelope, fail, ok
from app.models import User, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])
hasher = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1, type=Type.ID)


class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str


def _options() -> dict:
    return {"secure": get_settings().environment == "production", "samesite": "lax", "path": "/"}


def _set_session(response: Response, db, user: User) -> None:
    raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    db.add(UserSession(user_id=user.id, token_hash=hash_token(raw), csrf_hash=hash_token(csrf),
                       expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=SESSION_MAX_AGE)))
    db.commit()
    response.set_cookie(SESSION_COOKIE, raw, max_age=SESSION_MAX_AGE, httponly=True, **_options())
    response.set_cookie(CSRF_COOKIE, csrf, max_age=SESSION_MAX_AGE, httponly=False, **_options())


@router.post("/register", response_model=Envelope)
def register(body: Credentials, response: Response):
    username = body.username.strip()
    if not username or not body.password:
        return JSONResponse(status_code=422, content=fail("帳號與密碼不可為空").model_dump())
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is not None:
            return JSONResponse(status_code=409, content=fail("註冊已關閉").model_dump())
        user = User(username=username, username_normalized=username.casefold(), password_hash=hasher.hash(body.password), is_owner=True)
        db.add(user)
        try:
            db.flush()
            _set_session(response, db, user)
        except IntegrityError:
            db.rollback()
            return JSONResponse(status_code=409, content=fail("註冊已關閉").model_dump())
    return ok({"authenticated": True, "registration_open": False, "username": username})


@router.post("/login", response_model=Envelope)
def login(body: Credentials, request: Request, response: Response):
    client_id = client_identifier(request)
    retry = login_retry_after(client_id)
    if retry:
        return JSONResponse(status_code=429, content=fail("登入嘗試過多").model_dump(), headers={"Retry-After": str(retry)})
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username_normalized == body.username.strip().casefold()))
        try:
            valid = user is not None and hasher.verify(user.password_hash, body.password)
        except VerifyMismatchError:
            valid = False
        if not valid:
            record_failed_login(client_id)
            return JSONResponse(status_code=401, content=fail("帳號或密碼錯誤").model_dump())
        clear_failed_logins(client_id)
        _set_session(response, db, user)
        username = user.username
    return ok({"authenticated": True, "registration_open": False, "username": username})


@router.get("/session", response_model=Envelope)
def session(request: Request):
    auth_session = get_session(request.cookies.get(SESSION_COOKIE))
    with SessionLocal() as db:
        registration_open = db.scalar(select(User.id).limit(1)) is None
        user = db.get(User, auth_session.user_id) if auth_session else None
    return ok({"authenticated": user is not None, "registration_open": registration_open,
               "username": user.username if user else None})


@router.post("/logout", response_model=Envelope)
def logout(request: Request, response: Response):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        with SessionLocal() as db:
            session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw)))
            if session:
                db.delete(session)
                db.commit()
    response.delete_cookie(SESSION_COOKIE, httponly=True, **_options())
    response.delete_cookie(CSRF_COOKIE, httponly=False, **_options())
    return ok({"authenticated": False})
