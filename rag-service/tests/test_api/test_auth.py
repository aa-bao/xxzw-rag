from __future__ import annotations

import hashlib
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import Session as DbSession
from src.db.models import User
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
                "retry_base_delay_seconds": 1,
            },
            "database": {
                "url": database_url,
                "pool_size": 2,
                "pool_recycle_seconds": 1800,
            },
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


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_login_sets_opaque_cookie_and_database_stores_only_hash(
    migrated_mysql_url: str,
) -> None:
    username = _uniq("alice")
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=PasswordHasher().hash("secret"),
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
            response = await client.post(
                "/api/auth/login",
                json={"username": username, "password": "secret"},
            )

        assert response.status_code == 200
        cookie = response.cookies["rag_session"]
        set_cookie = response.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "samesite=strict" in set_cookie

        async with factory() as session:
            stored = (await session.execute(select(DbSession))).scalar_one()
        assert stored.token_hash == hashlib.sha256(cookie.encode()).hexdigest()
        assert cookie != stored.token_hash
    finally:
        await engine.dispose()


async def test_disabled_user_gets_same_login_error_as_unknown_user(
    migrated_mysql_url: str,
) -> None:
    username = _uniq("disabled")
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=PasswordHasher().hash("x"),
                    role="user",
                    status="disabled",
                )
            )
            await session.commit()

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            unknown_resp = await client.post(
                "/api/auth/login",
                json={"username": _uniq("missing"), "password": "x"},
            )
            disabled_resp = await client.post(
                "/api/auth/login",
                json={"username": username, "password": "x"},
            )

        assert unknown_resp.status_code == 401
        assert disabled_resp.status_code == 401
        assert unknown_resp.json()["error"]["code"] == disabled_resp.json()["error"]["code"]
        assert unknown_resp.json()["error"]["message"] == disabled_resp.json()["error"]["message"]
    finally:
        await engine.dispose()


async def test_login_is_rate_limited_without_changing_public_error(
    migrated_mysql_url: str,
) -> None:
    username = _uniq("ratelimit")
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=PasswordHasher().hash("secret"),
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
            for _ in range(5):
                resp = await client.post(
                    "/api/auth/login",
                    json={"username": username, "password": "wrong"},
                )
                assert resp.status_code == 401

            response = await client.post(
                "/api/auth/login",
                json={"username": username, "password": "wrong"},
            )

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    finally:
        await engine.dispose()


async def test_logout_clears_cookie_and_removes_session(
    migrated_mysql_url: str,
) -> None:
    username = _uniq("alice")
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=PasswordHasher().hash("secret"),
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
            login_resp = await client.post(
                "/api/auth/login",
                json={"username": username, "password": "secret"},
            )
            cookie = login_resp.cookies["rag_session"]

            logout_resp = await client.post(
                "/api/auth/logout",
                cookies={"rag_session": cookie},
            )

        assert logout_resp.status_code == 200
        assert logout_resp.json()["success"] is True

        set_cookie = logout_resp.headers.get("set-cookie", "")
        assert "rag_session=" in set_cookie or "Max-Age=0" in set_cookie

        async with factory() as session:
            token_hash = hashlib.sha256(cookie.encode()).hexdigest()
            stored = await session.scalar(
                select(DbSession).where(DbSession.token_hash == token_hash)
            )
        assert stored is None
    finally:
        await engine.dispose()


async def test_me_returns_current_user_when_authenticated(
    migrated_mysql_url: str,
) -> None:
    username = _uniq("alice")
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                User(
                    username=username,
                    password_hash=PasswordHasher().hash("secret"),
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
            login_resp = await client.post(
                "/api/auth/login",
                json={"username": username, "password": "secret"},
            )
            cookie = login_resp.cookies["rag_session"]

            me_resp = await client.get(
                "/api/auth/me",
                cookies={"rag_session": cookie},
            )

        assert me_resp.status_code == 200
        data = me_resp.json()["data"]
        assert data["username"] == username
        assert data["role"] == "user"
        assert "id" in data
    finally:
        await engine.dispose()


async def test_me_returns_401_when_not_authenticated(
    migrated_mysql_url: str,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            me_resp = await client.get("/api/auth/me")

        assert me_resp.status_code == 401
        assert me_resp.json()["error"]["code"] == "AUTH_REQUIRED"
    finally:
        await engine.dispose()
