from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory
from src.auth.passwords import PasswordHasher
from src.auth.rate_limiter import (
    clear_login_attempts,
    is_login_rate_limited,
    login_rate_limit_key,
    record_login_failure,
)
from src.auth.sessions import IssuedSession, SessionService
from src.db.models import Session as DbSession
from src.db.models import User
from src.shared.errors import AppError

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$YS0oGGj2ABX3BWFAphSfQA$"
    "Xxk+Rx7UOZBpOmg8KApL3uQmcKw42vT9oFkHePqAVpQ"
)


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    username = body.username.strip()
    client_ip = request.client.host if request.client else "127.0.0.1"
    rate_key = login_rate_limit_key(username, client_ip)

    if is_login_rate_limited(rate_key):
        hasher = PasswordHasher()
        hasher.verify(_DUMMY_HASH, body.password)
        raise AppError("AUTH_INVALID_CREDENTIALS", "用户名或密码错误", status_code=429)

    result = await db.execute(
        select(User.id, User.password_hash, User.role, User.status).where(
            User.username == username
        )
    )
    row = result.one_or_none()

    if row is None:
        hasher = PasswordHasher()
        hasher.verify(_DUMMY_HASH, body.password)
        record_login_failure(rate_key)
        raise AppError("AUTH_INVALID_CREDENTIALS", "用户名或密码错误", status_code=401)

    user_id, password_hash, user_role, user_status = row

    if user_status != "active":
        hasher = PasswordHasher()
        hasher.verify(_DUMMY_HASH, body.password)
        record_login_failure(rate_key)
        raise AppError("AUTH_INVALID_CREDENTIALS", "用户名或密码错误", status_code=401)

    hasher = PasswordHasher()
    if not hasher.verify(password_hash, body.password):
        record_login_failure(rate_key)
        raise AppError("AUTH_INVALID_CREDENTIALS", "用户名或密码错误", status_code=401)

    clear_login_attempts(rate_key)
    issued = await SessionService(db).issue(user_id)
    response.set_cookie(
        key="rag_session",
        value=issued.raw_token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    return {"success": True, "data": {"id": user_id, "username": username, "role": user_role}}


@router.post("/logout")
async def logout(
    response: Response,
    db: AsyncSession = Depends(_session_factory),
    rag_session: str | None = Cookie(default=None),
) -> dict[str, object]:
    if rag_session is not None:
        await SessionService(db).revoke(rag_session)
    response.delete_cookie(key="rag_session", path="/")
    return {"success": True, "data": None}


@router.get("/me")
async def me(
    db: AsyncSession = Depends(_session_factory),
    rag_session: str | None = Cookie(default=None),
) -> dict[str, object]:
    if rag_session is None:
        raise AppError("AUTH_REQUIRED", "请登录", status_code=401)
    token_hash = IssuedSession.hash_token(rag_session)
    now = datetime.now(UTC)
    result = await db.execute(
        select(User.id, User.username, User.role).join(
            DbSession,
            User.id == DbSession.user_id,
        ).where(
            DbSession.token_hash == token_hash,
            DbSession.expires_at > now,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise AppError("AUTH_REQUIRED", "请登录", status_code=401)
    user_id, username, role = row
    return {"success": True, "data": {"id": user_id, "username": username, "role": role}}
