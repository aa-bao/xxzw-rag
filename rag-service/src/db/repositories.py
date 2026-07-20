from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.shared.errors import AppError


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_first_admin(self, username: str, password_hash: str) -> User:
        normalized = username.strip()
        if not normalized:
            raise AppError("USERNAME_REQUIRED", "用户名不能为空")

        user_count = await self._session.scalar(select(func.count(User.id)))
        if user_count:
            raise AppError(
                "ADMIN_ALREADY_INITIALIZED",
                "首个账户管理员已经初始化",
                status_code=409,
            )

        user = User(
            username=normalized,
            password_hash=password_hash,
            role="account_admin",
            status="active",
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

