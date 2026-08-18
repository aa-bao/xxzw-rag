"""视频 agent 设置 API 集成测试（settings 往返 / 密钥不回显 / DB 持久化恢复）。"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import User, VideoSetting
from src.db.session import create_engine, create_session_factory
from tests.test_api.test_settings import _settings  # 复用模型配置构造


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


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert response.status_code == 200
    return {"rag_session": response.cookies["rag_session"]}


@pytest.fixture(autouse=True)
async def _clean_video_setting(migrated_mysql_url: str):
    """每个测试前清空 video_setting：共享库下防残留配置污染。"""
    engine = create_engine(migrated_mysql_url, pool_size=1)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(delete(VideoSetting))
            await session.commit()
    finally:
        await engine.dispose()
    yield


async def test_video_settings_defaults_and_no_key_echo(migrated_mysql_url: str) -> None:
    """GET 返回默认值；密钥字段绝不回显。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
            cookies = await _login(client, admin.username)
            response = await client.get("/api/video/settings", cookies=cookies)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["asr_provider"] == "volcengine"
        assert data["asr_model"] == "bigmodel"
        assert data["has_asr_api_key"] is False
        assert data["has_chat_api_key"] is False
        assert data["frames"] == 12
        # 密钥明文绝不回显
        assert "asr_api_key" not in data
        assert "asr_access_token" not in data
        assert "chat_api_key" not in data
    finally:
        await engine.dispose()


async def test_video_settings_put_persists_and_restores(migrated_mysql_url: str) -> None:
    """PUT 更新 → DB 持久化 → 新 app 实例从 DB 恢复。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
            cookies = await _login(client, admin.username)
            response = await client.put(
                "/api/video/settings",
                json={
                    "asr_model": "bigmodel-custom",
                    "asr_api_key": "volc-secret-key",
                    "chat_base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "chat_model": "doubao-seed-2-1-turbo-260628",
                    "chat_api_key": "ark-test-key",
                    "frames": 24,
                },
                cookies=cookies,
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["asr_model"] == "bigmodel-custom"
        assert data["has_asr_api_key"] is True
        assert data["has_chat_api_key"] is True
        assert data["frames"] == 24
        assert "asr_api_key" not in data
        assert "chat_api_key" not in data

        # DB 已持久化（startup 恢复逻辑在真实服务器重启中验证）
        from sqlalchemy import select

        async with factory() as session:
            stored = await session.scalar(select(VideoSetting).where(VideoSetting.id == 1))
        assert stored is not None
        assert stored.asr_model == "bigmodel-custom"
        assert stored.asr_api_key == "volc-secret-key"
        assert stored.chat_api_key == "ark-test-key"
        assert stored.frames == 24
    finally:
        await engine.dispose()


async def test_video_settings_empty_key_keeps_old_value(migrated_mysql_url: str) -> None:
    """asr_api_key 空串 = 不更新（保留原值）。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_admin(factory)
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
            cookies = await _login(client, admin.username)
            await client.put("/api/video/settings", json={"asr_api_key": "first-key"}, cookies=cookies)
            # 空串更新 → 保留 first-key
            response = await client.put("/api/video/settings", json={"asr_api_key": ""}, cookies=cookies)
            data = response.json()["data"]
            assert data["has_asr_api_key"] is True
    finally:
        await engine.dispose()


async def test_video_settings_requires_admin(migrated_mysql_url: str) -> None:
    """非管理员无法读写设置。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    async with factory() as session:
        user = User(
            username=_uniq("emp"),
            password_hash=PasswordHasher().hash("secret123"),
            role="user",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
            cookies = await _login(client, user.username)
            get_resp = await client.get("/api/video/settings", cookies=cookies)
            put_resp = await client.put("/api/video/settings", json={"frames": 8}, cookies=cookies)

        assert get_resp.status_code == 403
        assert put_resp.status_code == 403
    finally:
        await engine.dispose()
