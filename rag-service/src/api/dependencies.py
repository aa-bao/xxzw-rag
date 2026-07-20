from __future__ import annotations

from fastapi import Cookie, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.sessions import SessionService
from src.shared.errors import AppError


async def _session_factory(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


async def require_user(
    request: Request,
    rag_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(_session_factory),
) -> int:
    user_id = await SessionService.authenticate(db, rag_session)
    if user_id is None:
        raise AppError("AUTH_REQUIRED", "请登录", status_code=401)
    return user_id
