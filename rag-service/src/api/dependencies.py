from __future__ import annotations

import logging
import os

from fastapi import Cookie, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.sessions import SessionService
from src.db.models import User
from src.shared.errors import AppError

logger = logging.getLogger(__name__)

# 本地开发跳过登录校验：DEV_AUTH_BYPASS=1 且 APP_ENV=development 时所有请求视为该用户
_DEV_AUTH_BYPASS_USER_ID = int(os.environ.get("DEV_AUTH_BYPASS_USER_ID", "1"))


def _dev_bypass_active() -> bool:
    if os.environ.get("DEV_AUTH_BYPASS") != "1":
        return False
    if os.environ.get("APP_ENV", "development") != "development":
        return False
    logger.warning("DEV_AUTH_BYPASS active — all requests authenticated as user %s", _DEV_AUTH_BYPASS_USER_ID)
    return True


async def _session_factory(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


async def require_user(
    request: Request,
    rag_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(_session_factory),
) -> int:
    if _dev_bypass_active():
        user = await db.scalar(select(User.id).where(User.id == _DEV_AUTH_BYPASS_USER_ID))
        if user is None:
            raise AppError(
                "DEV_AUTH_BYPASS_USER_NOT_FOUND",
                f"DEV_AUTH_BYPASS 用户 {_DEV_AUTH_BYPASS_USER_ID} 不存在，请先创建或设置 DEV_AUTH_BYPASS_USER_ID",
                status_code=500,
            )
        return user
    user_id = await SessionService.authenticate(db, rag_session)
    if user_id is None:
        raise AppError("AUTH_REQUIRED", "请登录", status_code=401)
    return user_id
