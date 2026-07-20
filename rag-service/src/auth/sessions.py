from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Session as DbSession

SESSION_EXPIRY_DAYS = 7
TOKEN_BYTES = 32


class IssuedSession:
    __slots__ = ("raw_token", "token_hash")

    def __init__(self, raw_token: str, token_hash: str) -> None:
        self.raw_token = raw_token
        self.token_hash = token_hash

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def issue(self, user_id: int) -> IssuedSession:
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        token_hash = IssuedSession.hash_token(raw_token)
        now = datetime.now(UTC)
        session = DbSession(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(days=SESSION_EXPIRY_DAYS),
            last_seen_at=now,
        )
        self._db.add(session)
        await self._db.commit()
        return IssuedSession(raw_token=raw_token, token_hash=token_hash)

    @staticmethod
    async def authenticate(db: AsyncSession, raw_token: str | None) -> int | None:
        if raw_token is None:
            return None
        token_hash = IssuedSession.hash_token(raw_token)
        now = datetime.now(UTC)
        return await db.scalar(
            select(DbSession.user_id).where(
                DbSession.token_hash == token_hash,
                DbSession.expires_at > now,
            )
        )

    async def revoke(self, raw_token: str) -> None:
        token_hash = IssuedSession.hash_token(raw_token)
        session = await self._db.scalar(
            select(DbSession).where(DbSession.token_hash == token_hash)
        )
        if session is not None:
            await self._db.delete(session)
            await self._db.commit()
