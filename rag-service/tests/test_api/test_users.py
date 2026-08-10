from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import User
from src.db.session import create_engine, create_session_factory
from src.shared.config import Settings

from test_auth import _settings


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _make_user(
    session_factory,
    *,
    username: str,
    password: str = "secret123",
    role: str = "user",
    status: str = "active",
    created_at: datetime | None = None,
) -> User:
    user = User(
        username=username,
        password_hash=PasswordHasher().hash(password),
        role=role,
        status=status,
        created_at=created_at,
    )
    async with session_factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"rag_session": response.cookies["rag_session"]}


async def test_create_user_success(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            username = _uniq("bob")
            response = await client.post(
                "/api/users",
                json={"username": username, "password": "secret123"},
                cookies=cookies,
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["username"] == username
        assert data["role"] == "user"
        assert data["status"] == "active"
        assert data["created_at"] is not None

        async with factory() as session:
            stored = await session.scalar(
                select(User).where(User.id == data["id"])
            )
        assert stored is not None
        assert stored.role == "user"
        assert stored.password_hash != "secret123"
        assert PasswordHasher().verify(stored.password_hash, "secret123")
    finally:
        await engine.dispose()


async def test_create_admin_requires_explicit_role(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.post(
                "/api/users",
                json={"username": _uniq("manager"), "password": "secret123"},
                cookies=cookies,
            )

        assert response.status_code == 200
        assert response.json()["data"]["role"] == "user"
    finally:
        await engine.dispose()


async def test_create_user_duplicate_username_conflicts(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    existing = await _make_user(factory, username=_uniq("bob"))
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.post(
                "/api/users",
                json={"username": existing.username, "password": "secret123"},
                cookies=cookies,
            )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "USERNAME_EXISTS"
    finally:
        await engine.dispose()


async def test_create_user_short_password_rejected(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.post(
                "/api/users",
                json={"username": _uniq("shortpw"), "password": "123"},
                cookies=cookies,
            )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
    finally:
        await engine.dispose()


async def test_regular_user_cannot_manage_users(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    employee = await _make_user(factory, username=_uniq("emp"))
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, employee.username, "secret123")
            list_response = await client.get("/api/users", cookies=cookies)
            create_response = await client.post(
                "/api/users",
                json={"username": _uniq("intruder"), "password": "secret123"},
                cookies=cookies,
            )

        assert list_response.status_code == 403
        assert list_response.json()["error"]["code"] == "FORBIDDEN"
        assert create_response.status_code == 403
    finally:
        await engine.dispose()


async def test_unauthenticated_request_is_rejected(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.get("/api/users")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    finally:
        await engine.dispose()


async def test_list_users_sorted_by_created_at(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    admin = await _make_user(
        factory,
        username=_uniq("admin"),
        role="account_admin",
        created_at=now,
    )
    first = await _make_user(
        factory,
        username=_uniq("first"),
        created_at=now + timedelta(minutes=1),
    )
    second = await _make_user(
        factory,
        username=_uniq("second"),
        created_at=now + timedelta(minutes=2),
    )
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.get("/api/users", cookies=cookies)
            assert response.status_code == 200
            rows = response.json()["data"]
            ids = [row["id"] for row in rows]
            # 共享库可能有其他测试残留用户——只验证目标用户的相对顺序（created_at 升序）
            positions = [ids.index(uid) for uid in (admin.id, first.id, second.id)]
            assert positions == sorted(positions)
            for row in rows:
                if row["id"] in (admin.id, first.id, second.id):
                    assert row["status"] == "active"
                    assert row["created_at"] is not None
    finally:
        await engine.dispose()


async def test_update_user_status_and_password(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    target = await _make_user(factory, username=_uniq("emp"))
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.put(
                f"/api/users/{target.id}",
                json={"status": "disabled", "password": "newpassword"},
                cookies=cookies,
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["status"] == "disabled"
            assert data["role"] == "user"

            login_resp = await client.post(
                "/api/auth/login",
                json={"username": target.username, "password": "newpassword"},
            )
            assert login_resp.status_code == 401
    finally:
        await engine.dispose()


async def test_update_user_not_found_returns_404(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.put(
                "/api/users/999999",
                json={"status": "disabled"},
                cookies=cookies,
            )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "USER_NOT_FOUND"
    finally:
        await engine.dispose()


async def test_cannot_disable_or_demote_self(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")

            disable_resp = await client.put(
                f"/api/users/{admin.id}",
                json={"status": "disabled"},
                cookies=cookies,
            )
            demote_resp = await client.put(
                f"/api/users/{admin.id}",
                json={"role": "user"},
                cookies=cookies,
            )

        assert disable_resp.status_code == 400
        assert disable_resp.json()["error"]["code"] == "SELF_DISABLE_FORBIDDEN"
        assert demote_resp.status_code == 400
        assert demote_resp.json()["error"]["code"] == "SELF_ROLE_CHANGE_FORBIDDEN"
    finally:
        await engine.dispose()


async def test_admin_can_change_own_password(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            response = await client.put(
                f"/api/users/{admin.id}",
                json={"password": "brand-new-pass"},
                cookies=cookies,
            )
            me_resp = await client.get("/api/auth/me", cookies=cookies)

        assert response.status_code == 200
        assert me_resp.status_code == 200
        assert me_resp.json()["data"]["username"] == admin.username
    finally:
        await engine.dispose()


async def test_update_user_role_validates_enum(migrated_mysql_url: str) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    admin = await _make_user(factory, username=_uniq("admin"), role="account_admin")
    target = await _make_user(factory, username=_uniq("emp"))
    try:
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            cookies = await _login(client, admin.username, "secret123")
            bad_resp = await client.put(
                f"/api/users/{target.id}",
                json={"role": "superadmin"},
                cookies=cookies,
            )

        assert bad_resp.status_code == 422
    finally:
        await engine.dispose()
