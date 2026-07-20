from __future__ import annotations

import json
import uuid

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import Conversation, KnowledgeBase, User
from src.db.session import create_engine, create_session_factory
from src.shared.config import Settings


def _settings(database_url: str) -> Settings:
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


async def test_conversation_cannot_use_other_users_kb(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    a = f"alice-{uuid.uuid4().hex[:8]}"
    b = f"bob-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            ua = User(username=a, password_hash=hasher.hash("s"), role="user", status="active")
            ub = User(username=b, password_hash=hasher.hash("s"), role="user", status="active")
            session.add_all([ua, ub])
            await session.flush()
            kb_b = KnowledgeBase(
                owner_user_id=ub.id, name="b-kb",
                embedding_model="t", embedding_dimension=128, active_collection="x",
            )
            session.add(kb_b)
            await session.commit()
            bob_kb = kb_b.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, a, "s")
            resp = await client.post(
                "/api/chat/conversations",
                json={"kb_id": bob_kb},
                cookies={"rag_session": cookie},
            )

        assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_query_schema_rejects_client_history_and_kb(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.flush()
            conv = Conversation(id=uuid.uuid4().hex, owner_user_id=u.id, kb_id=kb.id)
            session.add(conv)
            await session.commit()
            cid = conv.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            resp = await client.post(
                "/api/chat/query",
                json={
                    "conversation_id": cid, "question": "x",
                    "kb_id": 2, "history": [],
                },
                cookies={"rag_session": cookie},
            )

        assert resp.status_code == 422
    finally:
        await engine.dispose()


async def test_create_conversation_and_stream_answer(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")

            cr = await client.post(
                "/api/chat/conversations",
                json={"kb_id": kb_id},
                cookies={"rag_session": cookie},
            )
            assert cr.status_code == 200
            cid = cr.json()["data"]["id"]

            qr = await client.post(
                "/api/chat/query",
                json={"conversation_id": cid, "question": "测试"},
                cookies={"rag_session": cookie},
            )
            assert qr.status_code == 200
            body = qr.text
            assert "event: chunk" in body or "event: done" in body

        # List conversations
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            lr = await client.get("/api/chat/history", cookies={"rag_session": cookie})
            assert lr.status_code == 200
            assert len(lr.json()["data"]) == 1
    finally:
        await engine.dispose()
