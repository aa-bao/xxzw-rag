from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.passwords import PasswordHasher
from src.db.models import PlatformSession, User
from src.platform.identity import PlatformIdentity


PLATFORM_SESSION_COOKIE = "rag_database_session"
SESSION_MAX_AGE = timedelta(minutes=30)
REFRESH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    internal_user_id: int
    identity: PlatformIdentity
    refreshed_at: datetime
    session_id: int | None = None


class PlatformSessionService:
    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    async def issue(db: AsyncSession, identity: PlatformIdentity) -> tuple[str, int]:
        user = await db.scalar(
            select(User).where(
                User.platform_tenant_id == identity.tenantId,
                User.platform_user_id == identity.userId,
            )
        )
        if user is None:
            user = User(
                username=f"platform:{identity.tenantId}:{identity.userId}",
                password_hash=PasswordHasher().hash(secrets.token_urlsafe(32)),
                role="account_admin" if "superadmin" in identity.roles else "user",
                status="active",
                platform_tenant_id=identity.tenantId,
                platform_user_id=identity.userId,
                platform_department_id=identity.departmentId,
            )
            db.add(user)
            await db.flush()
        else:
            user.platform_department_id = identity.departmentId
            user.status = "active"
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC).replace(tzinfo=None)
        session = PlatformSession(
            user_id=user.id,
            token_hash=PlatformSessionService.hash_token(token),
            identity_json=identity.model_dump_json(),
            expires_at=now + SESSION_MAX_AGE,
            refreshed_at=now,
        )
        db.add(session)
        await db.flush()
        await db.commit()
        return token, user.id

    @staticmethod
    async def authenticate(
        db: AsyncSession, token: str | None
    ) -> AuthenticatedIdentity | None:
        if not token:
            return None
        now = datetime.now(UTC).replace(tzinfo=None)
        session = await db.scalar(
            select(PlatformSession).where(
                PlatformSession.token_hash == PlatformSessionService.hash_token(token),
                PlatformSession.expires_at > now,
            )
        )
        if session is None:
            return None
        return AuthenticatedIdentity(
            internal_user_id=session.user_id,
            identity=PlatformIdentity.model_validate_json(session.identity_json),
            refreshed_at=session.refreshed_at,
            session_id=session.id,
        )

    @staticmethod
    async def touch(
        db: AsyncSession, *, session_id: int, identity: PlatformIdentity
    ) -> None:
        """刷新后更新身份载荷与刷新时间；过期会话在此过程中被清理。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        await db.execute(
            update(PlatformSession)
            .where(
                PlatformSession.id == session_id,
                PlatformSession.expires_at > now,
            )
            .values(identity_json=identity.model_dump_json(), refreshed_at=now)
        )
        await db.commit()

    @staticmethod
    async def revoke(db: AsyncSession, token: str | None) -> None:
        if not token:
            return
        session = await db.scalar(
            select(PlatformSession).where(
                PlatformSession.token_hash == PlatformSessionService.hash_token(token)
            )
        )
        if session is not None:
            await db.delete(session)
            await db.commit()
