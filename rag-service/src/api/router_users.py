from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_admin
from src.auth.passwords import PasswordHasher
from src.db.models import User
from src.platform.principal import ProjectPrincipal
from src.shared.errors import AppError

router = APIRouter(prefix="/api/users", tags=["users"])

_MIN_PASSWORD_LENGTH = 6


class CreateUserRequest(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = Field(min_length=1, max_length=100)
    password: str
    role: Literal["user", "account_admin"] = "user"


class UpdateUserRequest(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["active", "disabled"] | None = None
    password: str | None = None
    role: Literal["user", "account_admin"] | None = None


def _serialize_user(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "status": user.status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _require_password_length(password: str) -> None:
    if not password:
        raise AppError("PASSWORD_REQUIRED", "密码不能为空")
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise AppError("PASSWORD_TOO_SHORT", f"密码长度至少 {_MIN_PASSWORD_LENGTH} 位")


@router.post("")
async def create_user(
    body: CreateUserRequest,
    principal: ProjectPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    username = body.username.strip()
    if not username:
        raise AppError("USERNAME_REQUIRED", "用户名不能为空")
    _require_password_length(body.password)

    existing = await db.scalar(select(User.id).where(User.username == username))
    if existing is not None:
        raise AppError("USERNAME_EXISTS", "用户名已存在", status_code=409)

    user = User(
        username=username,
        password_hash=PasswordHasher().hash(body.password),
        role=body.role,
        status="active",
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AppError("USERNAME_EXISTS", "用户名已存在", status_code=409) from None
    await db.refresh(user)
    return {"success": True, "data": _serialize_user(user)}


@router.get("")
async def list_users(
    principal: ProjectPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    result = await db.execute(select(User).order_by(User.created_at.asc()))
    users = result.scalars().all()
    return {"success": True, "data": [_serialize_user(user) for user in users]}


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    body: UpdateUserRequest,
    principal: ProjectPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise AppError("USER_NOT_FOUND", "用户不存在", status_code=404)

    if user_id == principal.internal_user_id:
        if body.role is not None and body.role != user.role:
            raise AppError("SELF_ROLE_CHANGE_FORBIDDEN", "不能修改当前登录账号的角色")
        if body.status == "disabled":
            raise AppError("SELF_DISABLE_FORBIDDEN", "不能禁用当前登录账号")

    if body.status is not None:
        user.status = body.status
    if body.role is not None:
        user.role = body.role
    if body.password is not None:
        _require_password_length(body.password)
        user.password_hash = PasswordHasher().hash(body.password)

    await db.commit()
    await db.refresh(user)
    return {"success": True, "data": _serialize_user(user)}
