from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import Document, KnowledgeBase, User
from src.db.session import create_engine, create_session_factory
from src.shared.config import Settings


def _settings(database_url: str, upload_root: str) -> Settings:
    return Settings.model_validate(
        {
            "app": {"browser_origin": "http://127.0.0.1:8000", "secure_cookie": False},
            "rag": {
                "chroma_mode": "persist",
                "chroma_persist_dir": "./data/chroma",
                "default_chunk_size": 64,
                "default_overlap": 2,
                "top_k": 5,
                "similarity_threshold": 0.2,
            },
            "model_relay": {
                "base_url": "http://127.0.0.1:9000/v1",
                "api_key": "test-key",
                "embedding_model": "test-embedding",
                "chat_model": "test-chat",
                "timeout_seconds": 60,
                "embedding_max_retries": 3,
                "chat_pre_stream_max_retries": 1,
                "retry_base_delay_seconds": 0.01,
            },
            "database": {"url": database_url, "pool_size": 2, "pool_recycle_seconds": 1800},
            "upload": {
                "root_dir": upload_root,
                "temp_dir": f"{upload_root}_tmp",
                "max_size_mb": 100,
                "allowed_extensions": ["txt"],
            },
            "llm": {
                "temperature": 0.7,
                "max_tokens": 4096,
                "max_history_tokens": 4096,
                "system_prompt": "system",
            },
            "empty_response": "empty",
        }
    )


@pytest.fixture
def upload_dirs(tmp_path: Path) -> tuple[str, str]:
    root = tmp_path / "uploads"
    temp = tmp_path / "tmp"
    root.mkdir()
    temp.mkdir()
    return str(root), str(temp)


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.cookies["rag_session"]


async def test_upload_uses_generated_storage_path(
    migrated_mysql_url: str, upload_dirs: tuple[str, str], tmp_path: Path
) -> None:
    upload_root, _ = upload_dirs
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            user = User(
                username=username,
                password_hash=hasher.hash("secret"),
                role="user",
                status="active",
            )
            session.add(user)
            await session.flush()

            kb = KnowledgeBase(
                owner_user_id=user.id,
                name="test-kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_test_v1",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(
            _settings(migrated_mysql_url, upload_root),
            session_factory=factory,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "secret")

            response = await client.post(
                f"/api/docs/upload?kb_id={kb_id}",
                files={"file": ("../../secrets.txt", b"safe content", "text/plain")},
                cookies={"rag_session": cookie},
            )

        assert response.status_code == 200, response.text
        doc_id = response.json()["data"]["doc_id"]

        async with factory() as session:
            from sqlalchemy import select
            doc = (await session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
            stored = Path(doc.file_path)
        assert "secrets.txt" not in str(stored)
        assert (Path(upload_root) / stored).read_bytes() == b"safe content"
    finally:
        await engine.dispose()


async def test_upload_to_other_users_kb_is_not_found(
    migrated_mysql_url: str, upload_dirs: tuple[str, str]
) -> None:
    upload_root, _ = upload_dirs
    hasher = PasswordHasher()
    alice = f"alice-{uuid.uuid4().hex[:8]}"
    bob = f"bob-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u_a = User(username=alice, password_hash=hasher.hash("secret"), role="user", status="active")
            u_b = User(username=bob, password_hash=hasher.hash("secret"), role="user", status="active")
            session.add_all([u_a, u_b])
            await session.flush()
            kb_bob = KnowledgeBase(
                owner_user_id=u_b.id, name="bob-kb",
                embedding_model="test", embedding_dimension=128,
                active_collection="kb_bob_v1",
            )
            session.add(kb_bob)
            await session.commit()
            bob_kb_id = kb_bob.id

        app = create_app(
            _settings(migrated_mysql_url, upload_root),
            session_factory=factory,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, alice, "secret")

            response = await client.post(
                f"/api/docs/upload?kb_id={bob_kb_id}",
                files={"file": ("x.txt", b"test", "text/plain")},
                cookies={"rag_session": cookie},
            )

        assert response.status_code == 404
    finally:
        await engine.dispose()
