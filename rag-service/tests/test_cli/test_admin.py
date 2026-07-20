from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from importlib import import_module

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from src.db.models import Document, DocumentJob, KnowledgeBase, Session, User


def _load_cli_app():
    try:
        module = import_module("cli")
    except ModuleNotFoundError:
        return None
    return getattr(module, "app", None)


async def _read_user(database_url: str, username: str) -> User | None:
    engine = create_async_engine(database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(User).where(User.username == username))
            return result.scalar_one_or_none()
    finally:
        await engine.dispose()


async def _clear_users(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(delete(DocumentJob))
            await session.execute(delete(Document))
            await session.execute(delete(KnowledgeBase))
            await session.execute(delete(Session))
            await session.execute(delete(User))
            await session.commit()
    finally:
        await engine.dispose()


def test_first_admin_is_created_without_echoing_password(
    mysql_url: str,
    monkeypatch,
) -> None:
    username = f"admin-{uuid.uuid4().hex[:8]}"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = mysql_url
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert migration.returncode == 0, migration.stdout + migration.stderr

    asyncio.run(_clear_users(mysql_url))

    monkeypatch.setenv("DATABASE_URL", mysql_url)
    monkeypatch.setenv("MODEL_RELAY_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("MODEL_RELAY_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setenv("CHAT_MODEL", "test-chat")

    cli_app = _load_cli_app()
    assert cli_app is not None, "CLI app has not been implemented"
    result = CliRunner().invoke(
        cli_app,
        ["admin", "create", username],
        input="strong passphrase\nstrong passphrase\n",
    )

    assert result.exit_code == 0, result.output
    assert "strong passphrase" not in result.output
    user = asyncio.run(_read_user(mysql_url, username))
    assert user is not None
    assert user.role == "account_admin"
    assert user.status == "active"
    assert user.password_hash.startswith("$argon2id$")
