from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import KnowledgeBase, User
from src.db.session import create_engine, create_session_factory
from src.retrieval.module import RetrievedChunk
from src.shared.config import Settings
from test_retrieval.fakes import FakeRetrieval


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


async def test_list_returns_all_enabled_kbs(
    migrated_mysql_url: str,
) -> None:
    """员工与管理员均可见全部启用中的知识库（不再按 owner 过滤）。"""
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

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, alice_username, "secret")

            response = await client.get("/api/kb", cookies={"rag_session": cookie})

        assert response.status_code == 200
        data = response.json()["data"]
        # 全量可见：其他用户的 KB 对 alice 可见（共享测试库中可能还有别的 KB）
        assert {"alice-kb", "bob-kb"} <= {kb["name"] for kb in data}
        assert any(kb["doc_count"] == 0 for kb in data)
    finally:
        await engine.dispose()


async def test_create_rejects_extra_fields(
    migrated_mysql_url: str,
) -> None:
    """POST /api/kb 拒绝请求体中的额外字段（如 owner_user_id）。"""
    hasher = PasswordHasher()
    username = f"admin-kb-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=hasher.hash("secret"),
                    role="account_admin",
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


async def test_create_requires_admin(
    migrated_mysql_url: str,
) -> None:
    """员工创建知识库返回 403。"""
    hasher = PasswordHasher()
    username = f"emp-kb-{uuid.uuid4().hex[:8]}"
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
                json={"name": "docs"},
                cookies={"rag_session": cookie},
            )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
    finally:
        await engine.dispose()


async def test_test_kb_forwards_similarity_threshold(
    migrated_mysql_url: str,
) -> None:
    """POST /api/kb/{id}/test 把请求体中的 similarity_threshold 透传给 retrieval。"""
    hasher = PasswordHasher()
    username = f"admin-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            admin = User(
                username=username,
                password_hash=hasher.hash("secret"),
                role="account_admin",
                status="active",
            )
            session.add(admin)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=admin.id,
                name="test-kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_test_v1",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        retrieval = FakeRetrieval(
            [RetrievedChunk("c1", "content", 1, "doc", None, 0.8)]
        )
        app.state.ingest_chroma = retrieval
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "secret")
            response = await client.post(
                f"/api/kb/{kb_id}/test",
                json={"question": "query", "similarity_threshold": 0.6},
                cookies={"rag_session": cookie},
            )

        assert response.status_code == 200
        assert response.json()["data"][0]["chunk_id"] == "c1"
        assert retrieval.last_threshold == 0.6
        assert retrieval.last_kb == kb_id
    finally:
        await engine.dispose()
