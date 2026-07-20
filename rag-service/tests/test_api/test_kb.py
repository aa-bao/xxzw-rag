from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import KnowledgeBase, User
from src.db.session import create_engine, create_session_factory
from src.shared.config import Settings


def _settings(database_url: str) -> Settings:
    return Settings.model_validate(
        {
            "app": {"browser_origin": "http://127.0.0.1:8000", "secure_cookie": False},
            "rag": {
                "chroma_mode": "persist",
                "chroma_persist_dir": "./data/chroma",
                "default_chunk_size": 512,
                "default_overlap": 50,
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
                "root_dir": "./data/uploads",
                "temp_dir": "./data/tmp",
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


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.cookies["rag_session"]


async def test_list_returns_only_current_users_kbs(
    migrated_mysql_url: str,
) -> None:
    """Owner isolation: GET /api/kb returns only the authenticated user's KBs."""
    hasher = PasswordHasher()
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            alice = User(
                username=f"alice-kb-{uuid.uuid4().hex[:8]}",
                password_hash=hasher.hash("secret"),
                role="user",
                status="active",
            )
            bob = User(
                username=f"bob-kb-{uuid.uuid4().hex[:8]}",
                password_hash=hasher.hash("secret"),
                role="user",
                status="active",
            )
            session.add_all([alice, bob])
            await session.flush()

            kb_alice = KnowledgeBase(
                owner_user_id=alice.id,
                name="alice-kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_alice_v1",
            )
            kb_bob = KnowledgeBase(
                owner_user_id=bob.id,
                name="bob-kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_bob_v1",
            )
            session.add_all([kb_alice, kb_bob])
            await session.commit()

            alice_username = alice.username
            bob_username = bob.username

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, alice_username, "secret")

            response = await client.get("/api/kb", cookies={"rag_session": cookie})

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "alice-kb"
    finally:
        await engine.dispose()


async def test_create_rejects_client_identity(
    migrated_mysql_url: str,
) -> None:
    """POST /api/kb rejects owner_user_id in the request body."""
    hasher = PasswordHasher()
    username = f"alice-kb-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=hasher.hash("secret"),
                    role="user",
                    status="active",
                )
            )
            await session.commit()

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "secret")

            response = await client.post(
                "/api/kb",
                json={"name": "docs", "owner_user_id": 999},
                cookies={"rag_session": cookie},
            )

        assert response.status_code == 422
    finally:
        await engine.dispose()
