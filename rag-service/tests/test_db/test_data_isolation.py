"""数据隔离与平台身份集成测试（规范 10 §9.2、§14）。

- 19 位雪花 ID 全程字符串，不丢精度；
- RESTRICTED + 空集合 → 空集（失败关闭）；
- 租户隔离：跨租户不可见；LOCAL 兼容旧数据（NULL/空串 tenant）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import KnowledgeBase, User
from src.db.scope import scope_condition
from src.platform.principal import ProjectPrincipal

SNOWFLAKE_USER_ID = "2068630213954715649"
SNOWFLAKE_DEPT_ID = "2068630213954715650"
OTHER_TENANT = "000001"


def _principal(
    *,
    tenant: str,
    department: str = SNOWFLAKE_DEPT_ID,
    mode: str = "ALL",
    department_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
) -> ProjectPrincipal:
    return ProjectPrincipal(
        internal_user_id=1,
        tenant_id=tenant,
        external_user_id=SNOWFLAKE_USER_ID,
        department_id=department,
        display_name="Tester",
        data_scope_mode=mode,  # type: ignore[arg-type]
        data_scope_department_ids=frozenset(department_ids or []),
        data_scope_user_ids=frozenset(user_ids or []),
    )


async def _insert_owner(engine, tenant: str = "000000") -> int:
    """插入平台映射的本地用户并返回内部 id（owner FK 前置）。

    同一 (tenant, platform_user_id) 在 uk_rag_user_platform_identity 下唯一，
    已存在时复用其内部 id。
    """
    async with engine.begin() as connection:
        existing = await connection.scalar(
            select(User.id).where(
                User.platform_tenant_id == tenant,
                User.platform_user_id == SNOWFLAKE_USER_ID,
            )
        )
        if existing is not None:
            return int(existing)
        result = await connection.execute(
            User.__table__.insert().values(
                username=f"platform:{tenant}:{SNOWFLAKE_USER_ID}:{uuid.uuid4().hex[:6]}",
                password_hash="unused-hash",
                role="user",
                status="active",
                platform_tenant_id=tenant,
                platform_user_id=SNOWFLAKE_USER_ID,
                platform_department_id=SNOWFLAKE_DEPT_ID,
            )
        )
        return int(result.inserted_primary_key[0])


@pytest.mark.asyncio
async def test_migration_0006_0007_products_in_schema(migrated_mysql_url: str) -> None:
    engine = create_async_engine(migrated_mysql_url)
    try:
        async with engine.connect() as connection:
            tables, kb_columns, user_columns = await connection.run_sync(
                lambda sync_connection: (
                    set(inspect(sync_connection).get_table_names()),
                    {
                        column["name"]
                        for column in inspect(sync_connection).get_columns("rag_knowledge_base")
                    },
                    {
                        column["name"]
                        for column in inspect(sync_connection).get_columns("rag_user")
                    },
                )
            )
    finally:
        await engine.dispose()
    assert "rag_platform_session" in tables
    assert {"tenant_id", "department_id"} <= kb_columns
    assert {
        "platform_tenant_id",
        "platform_user_id",
        "platform_department_id",
    } <= user_columns


@pytest.mark.asyncio
async def test_snowflake_ids_roundtrip_without_precision_loss(migrated_mysql_url: str) -> None:
    engine = create_async_engine(migrated_mysql_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.insert().values(
                    username="platform:000000:2068630213954715649",
                    password_hash="unused-hash",
                    role="user",
                    status="active",
                    platform_tenant_id="000000",
                    platform_user_id=SNOWFLAKE_USER_ID,
                    platform_department_id=SNOWFLAKE_DEPT_ID,
                )
            )
        async with engine.connect() as connection:
            rows = (await connection.execute(select(User.platform_user_id))).scalars().all()
    finally:
        await engine.dispose()
    assert SNOWFLAKE_USER_ID in [str(value) for value in rows]
    # 19 位 ID 必须保持字符串原样（超出 Number.MAX_SAFE_INTEGER）
    assert any(isinstance(value, str) and value == SNOWFLAKE_USER_ID for value in rows)


@pytest.mark.asyncio
async def test_restricted_empty_scope_returns_empty_set(migrated_mysql_url: str) -> None:
    engine = create_async_engine(migrated_mysql_url)
    try:
        owner_id = await _insert_owner(engine)
        async with engine.begin() as connection:
            await connection.execute(
                KnowledgeBase.__table__.insert().values(
                    owner_user_id=owner_id,
                    tenant_id="000000",
                    department_id=SNOWFLAKE_DEPT_ID,
                    name="tenant-a-kb",
                    embedding_model="test",
                    embedding_dimension=768,
                    active_collection="kb_1_v1",
                )
            )
        principal = _principal(tenant="000000", mode="RESTRICTED", department_ids=[], user_ids=[])
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(KnowledgeBase.id).where(scope_condition(KnowledgeBase, principal))
                )
            ).scalars().all()
    finally:
        await engine.dispose()
    assert rows == []


@pytest.mark.asyncio
async def test_restricted_department_scope_filters_rows(migrated_mysql_url: str) -> None:
    engine = create_async_engine(migrated_mysql_url)
    try:
        owner_id = await _insert_owner(engine)
        async with engine.begin() as connection:
            await connection.execute(
                KnowledgeBase.__table__.insert().values(
                    [
                        {
                            "owner_user_id": owner_id,
                            "tenant_id": "000000",
                            "department_id": SNOWFLAKE_DEPT_ID,
                            "name": "dept-a-kb",
                            "embedding_model": "test",
                            "embedding_dimension": 768,
                            "active_collection": "kb_10_v1",
                        },
                        {
                            "owner_user_id": owner_id,
                            "tenant_id": "000000",
                            "department_id": "2068630213954715699",
                            "name": "other-dept-kb",
                            "embedding_model": "test",
                            "embedding_dimension": 768,
                            "active_collection": "kb_11_v1",
                        },
                    ]
                )
            )
        principal = _principal(
            tenant="000000", mode="RESTRICTED", department_ids=[SNOWFLAKE_DEPT_ID]
        )
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(KnowledgeBase.name).where(scope_condition(KnowledgeBase, principal))
                )
            ).scalars().all()
    finally:
        await engine.dispose()
    assert "dept-a-kb" in rows
    assert "other-dept-kb" not in rows


@pytest.mark.asyncio
async def test_tenant_isolation_blocks_other_tenant(migrated_mysql_url: str) -> None:
    engine = create_async_engine(migrated_mysql_url)
    try:
        owner_id = await _insert_owner(engine)
        async with engine.begin() as connection:
            await connection.execute(
                KnowledgeBase.__table__.insert().values(
                    owner_user_id=owner_id,
                    tenant_id=OTHER_TENANT,
                    department_id=SNOWFLAKE_DEPT_ID,
                    name="other-tenant-kb",
                    embedding_model="test",
                    embedding_dimension=768,
                    active_collection="kb_20_v1",
                )
            )
        principal = _principal(tenant="000000")
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(KnowledgeBase.name).where(scope_condition(KnowledgeBase, principal))
                )
            ).scalars().all()
    finally:
        await engine.dispose()
    assert OTHER_TENANT not in rows
    assert "other-tenant-kb" not in rows


@pytest.mark.asyncio
async def test_local_mode_matches_legacy_null_tenant_rows(migrated_mysql_url: str) -> None:
    engine = create_async_engine(migrated_mysql_url)
    try:
        owner_id = await _insert_owner(engine)
        async with engine.begin() as connection:
            await connection.execute(
                KnowledgeBase.__table__.insert().values(
                    owner_user_id=owner_id,
                    tenant_id=None,
                    department_id=None,
                    name="legacy-local-kb",
                    embedding_model="test",
                    embedding_dimension=768,
                    active_collection="kb_30_v1",
                )
            )
        principal = _principal(tenant="")  # LOCAL 模式租户为空
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(KnowledgeBase.name).where(scope_condition(KnowledgeBase, principal))
                )
            ).scalars().all()
    finally:
        await engine.dispose()
    assert "legacy-local-kb" in rows
