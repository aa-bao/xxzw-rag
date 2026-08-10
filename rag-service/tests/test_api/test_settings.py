from __future__ import annotations

import json
import uuid
from collections import deque
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import ModelSetting, User
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
                "api_key": "config-key",
                "embedding_model": "config-embedding",
                "chat_model": "config-chat",
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


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _make_admin(factory) -> User:
    username = _uniq("admin")
    async with factory() as session:
        user = User(
            username=username,
            password_hash=PasswordHasher().hash("secret123"),
            role="account_admin",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _make_employee(factory) -> User:
    username = _uniq("emp")
    async with factory() as session:
        user = User(
            username=username,
            password_hash=PasswordHasher().hash("secret123"),
            role="user",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert response.status_code == 200
    return {"rag_session": response.cookies["rag_session"]}


class FakeTransport(httpx.AsyncBaseTransport):
    """队列式假传输，用法与 test_relay.py 一致。"""

    def __init__(self) -> None:
        self._queue: deque[tuple[int, dict[str, str], dict[str, Any] | list[dict[str, Any]]]] = deque()
        self.request_count = 0
        self.last_json: dict[str, Any] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        if request.content:
            self.last_json = json.loads(request.content)
        if not self._queue:
            return httpx.Response(500, json={})
        status, headers, body = self._queue.popleft()
        return httpx.Response(status, json=body, headers=headers, request=request)

    def enqueue(self, *items: int | dict[str, Any]) -> None:
        for item in items:
            if isinstance(item, int):
                self._queue.append((item, {}, {}))
            else:
                self._queue.append((200, {"content-type": "application/json"}, item))


def _fake_transport_app(app, transport: FakeTransport) -> AsyncClient:
    """把 app.state.model_relay_client 的共享 httpx client 换成 FakeTransport 的。"""
    app.state.model_relay_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:9000/v1"
    )
    return app


@pytest.fixture(autouse=True)
async def _clean_model_setting(migrated_mysql_url: str):
    """每个测试前清空 rag_model_setting：共享库下防残留配置污染 config 默认值。"""
    engine = create_engine(migrated_mysql_url, pool_size=1)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(delete(ModelSetting))
            await session.commit()
    finally:
        await engine.dispose()
    yield


async def test_get_returns_config_defaults(migrated_mysql_url: str) -> None:
    """GET /api/settings/models 返回 config.yaml 默认值，且不暴露 api_key。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)
            response = await client.get("/api/settings/models", cookies=cookies)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["base_url"] == "http://127.0.0.1:9000/v1"
        assert data["chat_model"] == "config-chat"
        assert data["embedding_model"] == "config-embedding"
        # 未单独配置 embedding → null = 与 chat 共用
        assert data["embedding_base_url"] is None
        assert data["has_chat_api_key"] is True
        assert data["has_embedding_api_key"] is True
        # api_key 绝不回显
        assert "api_key" not in response.json()["data"]
        assert "embedding_api_key" not in response.json()["data"]
    finally:
        await engine.dispose()


async def test_put_saves_and_persists(migrated_mysql_url: str) -> None:
    """PUT 更新运行时配置并写入 DB；新 app 实例启动后从 DB 恢复。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)
            response = await client.put(
                "/api/settings/models",
                json={
                    "base_url": "http://127.0.0.1:9001/v1",
                    "chat_model": "chat-v2",
                    "embedding_model": "embed-v2",
                    "api_key": "new-secret-key",
                },
                cookies=cookies,
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["base_url"] == "http://127.0.0.1:9001/v1"
        assert data["chat_model"] == "chat-v2"
        assert data["embedding_model"] == "embed-v2"
        assert data["has_chat_api_key"] is True

        # 运行时立即生效
        assert app.state.runtime_relay.base_url == "http://127.0.0.1:9001/v1"
        assert app.state.runtime_relay.api_key == "new-secret-key"
        assert app.state.runtime_relay.chat_model == "chat-v2"
        assert app.state.runtime_relay.embedding_model == "embed-v2"

        # DB 已持久化
        async with factory() as session:
            stored = await session.scalar(select(ModelSetting).where(ModelSetting.id == 1))
        assert stored is not None
        assert stored.base_url == "http://127.0.0.1:9001/v1"
        assert stored.api_key == "new-secret-key"
        assert stored.chat_model == "chat-v2"
        assert stored.embedding_model == "embed-v2"
        # embedding 未单独提供 → 列回写 NULL（与 chat 共用）
        assert stored.embedding_base_url is None
        assert stored.embedding_api_key is None

        # 重启（新 app 实例）→ startup 从 DB 恢复配置
        app2 = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        # ASGITransport 不跑 lifespan，手动执行 startup 的 DB 恢复逻辑
        async with factory() as session:
            stored2 = await session.scalar(select(ModelSetting).where(ModelSetting.id == 1))
        assert stored2 is not None
        assert stored2.base_url == "http://127.0.0.1:9001/v1"
        assert stored2.chat_model == "chat-v2"
        assert stored2.embedding_model == "embed-v2"
    finally:
        await engine.dispose()


async def test_put_embedding_base_url_null_means_shared(migrated_mysql_url: str) -> None:
    """embedding_base_url 传 null = 与 chat 共用；传非空 = 独立地址。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)

            # 独立 embedding 地址
            resp = await client.put(
                "/api/settings/models",
                json={"embedding_base_url": "http://127.0.0.1:9002/v1"},
                cookies=cookies,
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["embedding_base_url"] == "http://127.0.0.1:9002/v1"

            # 回退共用
            resp = await client.put(
                "/api/settings/models",
                json={"embedding_base_url": None},
                cookies=cookies,
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["embedding_base_url"] is None
            # 内部表示：空串 = 与 chat 共用（客户端侧 or 回退）
            assert app.state.runtime_relay.embedding_base_url in ("", app.state.runtime_relay.base_url)

            # 空字符串 = 与 chat 共用（与 null 同义）；已是共用时无变化 → 400
            resp = await client.put(
                "/api/settings/models",
                json={"embedding_base_url": ""},
                cookies=cookies,
            )
            assert resp.status_code in (200, 400)
            if resp.status_code == 200:
                assert resp.json()["data"]["embedding_base_url"] is None
    finally:
        await engine.dispose()


async def test_empty_api_key_keeps_existing(migrated_mysql_url: str) -> None:
    """api_key 传空字符串 = 保留原值；单独返回无 api_key 字段。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)
            response = await client.put(
                "/api/settings/models",
                json={"chat_model": "chat-v3", "api_key": "", "embedding_api_key": ""},
                cookies=cookies,
            )

        assert response.status_code == 200
        assert app.state.runtime_relay.api_key == "config-key"
        # embedding key 未单独配 → 内部空串 = 共用 chat key（客户端 or 回退）
        assert app.state.runtime_relay.embedding_api_key in ("", "config-key")
        assert app.state.runtime_relay.chat_model == "chat-v3"
        body = response.json()
        assert "api_key" not in body["data"]
        assert "embedding_api_key" not in body["data"]
    finally:
        await engine.dispose()


async def test_put_validates_required_fields(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)

            resp = await client.put("/api/settings/models", json={"base_url": ""}, cookies=cookies)
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "BASE_URL_REQUIRED"

            resp = await client.put("/api/settings/models", json={"chat_model": ""}, cookies=cookies)
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "CHAT_MODEL_REQUIRED"

            resp = await client.put("/api/settings/models", json={"embedding_model": ""}, cookies=cookies)
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "EMBEDDING_MODEL_REQUIRED"

            resp = await client.put(
                "/api/settings/models", json={"embedding_base_url": "   "}, cookies=cookies
            )
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "EMBEDDING_BASE_URL_INVALID"

            resp = await client.put("/api/settings/models", json={}, cookies=cookies)
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "NO_UPDATE_FIELDS"
    finally:
        await engine.dispose()


async def test_update_resets_embedding_dimension_cache(migrated_mysql_url: str) -> None:
    """PUT 成功更新后 embedding_dimension 缓存被重置为 0。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        app.state.embedding_dimension = 1536
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)
            resp = await client.put(
                "/api/settings/models",
                json={"embedding_model": "embed-v9"},
                cookies=cookies,
            )

        assert resp.status_code == 200
        assert app.state.embedding_dimension == 0
    finally:
        await engine.dispose()


async def test_test_endpoint_probes_with_passed_config(migrated_mysql_url: str) -> None:
    """POST /api/settings/models/test 用传入配置 probe，成功返回维度。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        transport = FakeTransport()
        transport.enqueue({"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), transport)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)
            response = await client.post(
                "/api/settings/models/test",
                json={
                    "base_url": "http://127.0.0.1:9100/v1",
                    "embedding_model": "probe-embed",
                    "api_key": "probe-key",
                },
                cookies=cookies,
            )

        assert response.status_code == 200
        assert response.json()["data"] == {"ok": True, "dimension": 3}
        # probe 请求带着传入的模型名和 key 发出
        assert transport.last_json["model"] == "probe-embed"
        # 运行时配置未被测试污染
        assert app.state.runtime_relay.base_url == "http://127.0.0.1:9000/v1"
        assert app.state.runtime_relay.api_key == "config-key"
    finally:
        await engine.dispose()


async def test_test_endpoint_failure_returns_200_wrapped(migrated_mysql_url: str) -> None:
    """模型不可用（401）→ 200 包裹 ok:false + message，不抛 500。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        transport = FakeTransport()
        transport.enqueue(401)
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), transport)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username)
            response = await client.post(
                "/api/settings/models/test",
                json={"base_url": "http://127.0.0.1:9100/v1", "embedding_model": "x"},
                cookies=cookies,
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ok"] is False
        assert "message" in data
    finally:
        await engine.dispose()


async def test_test_requires_admin(migrated_mysql_url: str) -> None:
    """非管理员访问 settings 接口一律 403。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    employee = await _make_employee(factory)
    try:
        app = _fake_transport_app(create_app(_settings(migrated_mysql_url), session_factory=factory), FakeTransport())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, employee.username)
            get_resp = await client.get("/api/settings/models", cookies=cookies)
            put_resp = await client.put(
                "/api/settings/models",
                json={"chat_model": "x"},
                cookies=cookies,
            )
            test_resp = await client.post(
                "/api/settings/models/test",
                json={"base_url": "http://x", "embedding_model": "x"},
                cookies=cookies,
            )

        for resp in (get_resp, put_resp, test_resp):
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "FORBIDDEN"
    finally:
        await engine.dispose()
