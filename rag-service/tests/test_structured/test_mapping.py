"""映射服务测试：版本管理、不可变版本、路径校验与文档绑定。

需要真实 MySQL（migrated_mysql_url fixture，Docker 启动约 60s）。
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.auth.passwords import PasswordHasher
from src.db.models import (
    Document,
    DocumentJob,
    DocumentMapping,
    KnowledgeBase,
    MappingTemplate,
    MappingTemplateVersion,
    User,
)
from src.db.session import create_engine, create_session_factory
from src.shared.errors import AppError
from src.structured.mapping import MappingService
from src.structured.models import MappingDefinition


def _valid_definition() -> MappingDefinition:
    return MappingDefinition.model_validate(
        {
            "name": "帖子模板",
            "source_format": "json",
            "record_types": [
                {
                    "name": "post",
                    "record_path": "$",
                    "fields": [
                        {"path": "id", "role": "id"},
                        {"path": "title", "role": "title"},
                        {"path": "body", "role": "content"},
                    ],
                }
            ],
        }
    )


async def _ensure_user(session_factory, username: str) -> User:
    async with session_factory() as session:
        user = User(
            username=username,
            password_hash=PasswordHasher().hash("secret"),
            role="user",
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _ensure_kb(session_factory, owner: User) -> KnowledgeBase:
    async with session_factory() as session:
        kb = KnowledgeBase(
            owner_user_id=owner.id,
            name="测试库",
            description=None,
            embedding_model="test-model",
            embedding_dimension=256,
            active_collection="kb_test_v1",
            chunk_size=512,
            overlap=50,
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        return kb


async def _ensure_document(session_factory, kb: KnowledgeBase) -> Document:
    async with session_factory() as session:
        doc = Document(
            kb_id=kb.id,
            owner_user_id=kb.owner_user_id,
            title="posts.json",
            source="upload",
            file_path=f"upload/{uuid.uuid4().hex}.json",
            status="awaiting_mapping",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


async def _template_and_service(
    migrated_mysql_url: str,
) -> tuple[MappingTemplate, MappingService, AsyncEngine, int]:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    user = await _ensure_user(factory, f"mapper-{uuid.uuid4().hex[:8]}")

    service = MappingService(factory, user.id)
    template = await service.create_template(_valid_definition())
    return template, service, engine, user.id


@pytest.mark.asyncio
async def test_create_version_first_is_one(migrated_mysql_url: str) -> None:
    template, service, engine, _ = await _template_and_service(migrated_mysql_url)
    try:
        version = await service.create_version(template.id, _valid_definition())
        assert version.version == 1
        assert version.structure_fingerprint
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_changed_definition_creates_version_two(migrated_mysql_url: str) -> None:
    template, service, engine, _ = await _template_and_service(migrated_mysql_url)
    try:
        first = await service.create_version(template.id, _valid_definition())

        changed = MappingDefinition.model_validate(
            {
                "name": "帖子模板 v2",
                "source_format": "json",
                "record_types": [
                    {
                        "name": "post",
                        "record_path": "$",
                        "fields": [
                            {"path": "id", "role": "id"},
                            {"path": "title", "role": "title"},
                            {"path": "body", "role": "content"},
                            {"path": "tags", "role": "keyword"},
                        ],
                    }
                ],
            }
        )
        second = await service.create_version(template.id, changed)

        assert first.version == 1
        assert second.version == 2
        assert first.structure_fingerprint != second.structure_fingerprint
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_definition_reuses_latest_version(migrated_mysql_url: str) -> None:
    template, service, engine, _ = await _template_and_service(migrated_mysql_url)
    try:
        first = await service.create_version(template.id, _valid_definition())
        second = await service.create_version(template.id, _valid_definition())

        assert second.version == 1
        assert second.id == first.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_updating_used_version_raises_immutable(migrated_mysql_url: str) -> None:
    template, service, engine, user_id = await _template_and_service(migrated_mysql_url)
    try:
        version = await service.create_version(template.id, _valid_definition())

        # 绑定文档使该版本被使用
        factory = create_session_factory(engine)
        kb = await _ensure_kb(factory, (await _get_user(factory, user_id)))
        doc = await _ensure_document(factory, kb)
        await service.bind_document(
            document_id=doc.id, mapping_version_id=version.id, confirmed_by_user_id=user_id
        )

        with pytest.raises(AppError) as excinfo:
            await service.update_version(
                template.id, version.id, _valid_definition()
            )
        assert excinfo.value.code == "MAPPING_VERSION_IMMUTABLE"
    finally:
        await engine.dispose()


async def _get_user(session_factory, user_id: int) -> User:
    async with session_factory() as session:
        return (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_bind_document_stores_confirmer_and_queues(migrated_mysql_url: str) -> None:
    template, service, engine, user_id = await _template_and_service(migrated_mysql_url)
    try:
        version = await service.create_version(template.id, _valid_definition())

        factory = create_session_factory(engine)
        kb = await _ensure_kb(factory, (await _get_user(factory, user_id)))
        doc = await _ensure_document(factory, kb)

        mapping = await service.bind_document(
            document_id=doc.id, mapping_version_id=version.id, confirmed_by_user_id=user_id
        )

        assert mapping.confirmed_by_user_id == user_id
        assert mapping.mapping_version_id == version.id

        async with factory() as session:
            stored = (
                await session.execute(select(Document).where(Document.id == doc.id))
            ).scalar_one()
            assert stored.status == "queued"
            jobs = (
                await session.execute(
                    select(DocumentJob).where(DocumentJob.doc_id == doc.id)
                )
            ).scalars().all()
            assert len(jobs) == 1
            assert jobs[0].job_type == "ingest"
            assert jobs[0].status == "pending"

            mapping_row = (
                await session.execute(
                    select(DocumentMapping).where(DocumentMapping.document_id == doc.id)
                )
            ).scalar_one()
            assert mapping_row.mapping_version_id == version.id
            assert mapping_row.confirmed_by_user_id == user_id
            assert mapping_row.confirmed_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bind_document_does_not_queue_on_invalid_definition(
    migrated_mysql_url: str,
) -> None:
    """绑定前必须校验定义；定义非法时文档保持 awaiting_mapping，不产生作业。"""
    template, service, engine, user_id = await _template_and_service(migrated_mysql_url)
    try:
        invalid = MappingDefinition.model_validate(
            {
                "name": "缺正文模板",
                "source_format": "json",
                "record_types": [
                    {
                        "name": "post",
                        "record_path": "$",
                        "fields": [
                            {"path": "id", "role": "id"},
                            {"path": "title", "role": "ignore"},
                        ],
                    }
                ],
            }
        )
        with pytest.raises(AppError) as excinfo:
            await service.create_version(template.id, invalid)
        assert excinfo.value.code == "MAPPING_REQUIRES_TEXT"

        factory = create_session_factory(engine)
        kb = await _ensure_kb(factory, (await _get_user(factory, user_id)))
        doc = await _ensure_document(factory, kb)

        version = await service.create_version(template.id, _valid_definition())
        with pytest.raises(AppError) as excinfo2:
            await service.bind_document(
                document_id=doc.id, mapping_version_id=999999, confirmed_by_user_id=user_id
            )
        assert excinfo2.value.code == "MAPPING_VERSION_NOT_FOUND"

        async with factory() as session:
            stored = (
                await session.execute(select(Document).where(Document.id == doc.id))
            ).scalar_one()
            assert stored.status == "awaiting_mapping"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_compile_path_validation_rejects_bad_path(migrated_mysql_url: str) -> None:
    template, service, engine, _ = await _template_and_service(migrated_mysql_url)
    try:
        bad = MappingDefinition.model_validate(
            {
                "name": "坏路径模板",
                "source_format": "json",
                "record_types": [
                    {
                        "name": "post",
                        "record_path": "$..posts",  # 递归下降被禁止
                        "fields": [{"path": "id", "role": "id"}],
                    }
                ],
            }
        )
        with pytest.raises(AppError) as excinfo:
            await service.create_version(template.id, bad)
        assert excinfo.value.code == "MAPPING_INVALID_PATH"
    finally:
        await engine.dispose()
