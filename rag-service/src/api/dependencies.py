from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Callable

from fastapi import Cookie, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.sessions import SessionService
from src.db.models import User
from src.platform.config import conformance_mode
from src.platform.principal import (
    PERMISSION_SETTINGS_MANAGE,
    ProjectPrincipal,
)
from src.platform.sessions import (
    PLATFORM_SESSION_COOKIE,
    PlatformSessionService,
    REFRESH_INTERVAL,
)
from src.shared.errors import AppError

logger = logging.getLogger(__name__)

# 本地开发跳过登录校验：DEV_AUTH_BYPASS=1 且 APP_ENV=development 时所有请求视为该用户
_DEV_AUTH_BYPASS_USER_ID = int(os.environ.get("DEV_AUTH_BYPASS_USER_ID", "1"))

PLATFORM_ENVIRONMENTS = ("TEST", "PRODUCTION")


def _dev_bypass_active() -> bool:
    if os.environ.get("DEV_AUTH_BYPASS") != "1":
        return False
    if os.environ.get("APP_ENV", "development") != "development":
        return False
    logger.warning(
        "DEV_AUTH_BYPASS active — all requests authenticated as user %s",
        _DEV_AUTH_BYPASS_USER_ID,
    )
    return True


async def _session_factory(request: Request) -> AsyncSession | None:
    """Conformance 模式无数据库：依赖被解析但不使用。"""
    if conformance_mode():
        yield None
        return
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


def _platform_environment(request: Request) -> str:
    return request.app.state.platform_config.environment


async def _platform_principal(
    request: Request, token: str | None, db: AsyncSession
) -> ProjectPrincipal:
    """TEST/PRODUCTION 只接受平台项目会话（禁止回退到本地 rag_session）。"""
    authenticated = await PlatformSessionService.authenticate(db, token)
    if authenticated is None:
        raise AppError("AUTH_REQUIRED", "请先登录", status_code=401)
    # 会话最长 30 分钟、至少每 5 分钟向控制面刷新平台权限；
    # 停用/部门变化/权限撤销在刷新周期内失败关闭。
    now = datetime.now(UTC).replace(tzinfo=None)
    client = getattr(request.app.state, "control_plane_client", None)
    if client is not None and now - authenticated.refreshed_at >= REFRESH_INTERVAL:
        try:
            refreshed = await client.refresh(authenticated.identity)
        except AppError as exc:
            if exc.status_code in (401, 403):
                raise AppError("AUTH_REQUIRED", "平台权限已失效，请重新登录", status_code=401) from None
            # 控制面暂时不可用（5xx/网络）：保守保留现有会话，下次请求再试
            logger.warning("platform permission refresh failed: %s", exc.code)
        else:
            await PlatformSessionService.touch(
                db, session_id=authenticated.session_id, identity=refreshed
            )
            authenticated = await PlatformSessionService.authenticate(db, token)
            if authenticated is None:
                raise AppError("AUTH_REQUIRED", "请先登录", status_code=401)
    return ProjectPrincipal.from_platform_identity(
        authenticated.identity, internal_user_id=authenticated.internal_user_id
    )


async def _conformance_principal(
    request: Request, token: str | None
) -> ProjectPrincipal:
    from src.api.router_platform import conformance_identity_for_session

    if token in (os.environ.get("TEST_EXPIRED_SESSION"), ""):
        raise AppError("AUTH_REQUIRED", "请先登录", status_code=401)
    identity = conformance_identity_for_session(token or "", request.app.state)
    if identity is None:
        raise AppError("AUTH_REQUIRED", "请先登录", status_code=401)
    return ProjectPrincipal.from_platform_identity(identity, internal_user_id=0)


async def require_user(
    request: Request,
    rag_session: str | None = Cookie(default=None),
    platform_token: str | None = Cookie(default=None, alias=PLATFORM_SESSION_COOKIE),
    db: AsyncSession = Depends(_session_factory),
) -> ProjectPrincipal:
    if _dev_bypass_active():
        return ProjectPrincipal.for_local_user(_DEV_AUTH_BYPASS_USER_ID, role="account_admin")
    if conformance_mode():
        token = platform_token or request.headers.get("X-Test-Session")
        return await _conformance_principal(request, token)
    environment = _platform_environment(request)
    if environment in PLATFORM_ENVIRONMENTS:
        return await _platform_principal(request, platform_token, db)
    # LOCAL：优先平台会话（本地对接真实控制面时），否则兼容原有本地账号
    if platform_token:
        return await _platform_principal(request, platform_token, db)
    user_id = await SessionService.authenticate(db, rag_session)
    if user_id is None:
        raise AppError("AUTH_REQUIRED", "请先登录", status_code=401)
    role = await db.scalar(select(User.role).where(User.id == user_id))
    return ProjectPrincipal.for_local_user(user_id, role=role or "user")


async def require_admin(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> ProjectPrincipal:
    """平台模式校验 settings:manage 权限字符；LOCAL 模式校验本地 account_admin 角色。"""
    if _platform_environment(request) in PLATFORM_ENVIRONMENTS:
        principal.require_permission(PERMISSION_SETTINGS_MANAGE)
        return principal
    if "account_admin" not in principal.roles:
        raise AppError("FORBIDDEN", "需要管理员权限", status_code=403)
    return principal


def require_permission(permission: str) -> Callable[..., ProjectPrincipal]:
    async def _dependency(
        principal: ProjectPrincipal = Depends(require_user),
    ) -> ProjectPrincipal:
        principal.require_permission(permission)
        return principal

    return _dependency
