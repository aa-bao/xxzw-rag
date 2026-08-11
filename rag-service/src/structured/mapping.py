"""版本化映射模板服务：模板、版本、结构指纹、不可变性与文档绑定。

规则（设计文档第 7 节）：
- 模板版本一旦被文档使用即不可修改（MAPPING_VERSION_IMMUTABLE）。
- mapping_json 规范化（排序键）后参与结构指纹，指纹不含实际值。
- 保存前用 compile_path 校验所有记录/字段路径。
- 每个被索引的记录类型必须至少有一个 content 或 title 字段。
- bind_document 在同一事务内创建 DocumentMapping、把文档置为 queued
  并创建 ingest 作业；校验失败时不产生任何副作用。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import (
    Document,
    DocumentJob,
    DocumentMapping,
    MappingTemplate,
    MappingTemplateVersion,
)
from src.shared.errors import AppError
from src.structured.models import MappingDefinition
from src.structured.paths import PathSyntaxError, compile_path


def canonicalize_definition(definition: MappingDefinition) -> str:
    """规范化 mapping_json：排序键，保证同一逻辑定义的 JSON 完全一致。"""
    return json.dumps(
        definition.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def definition_fingerprint(definition: MappingDefinition) -> str:
    """结构指纹：规范化 JSON 的 SHA-256（不含文件实际值）。"""
    return hashlib.sha256(canonicalize_definition(definition).encode("utf-8")).hexdigest()


def validate_definition(definition: MappingDefinition) -> None:
    """校验定义：所有路径合法 + 每个被索引记录类型有 content/title。"""
    for record_type in definition.record_types:
        try:
            compile_path(record_type.record_path)
        except PathSyntaxError as exc:
            raise AppError("MAPPING_INVALID_PATH", f"记录路径非法: {record_type.record_path}: {exc}") from exc
        for child in record_type.children:
            try:
                compile_path(child.record_path, relative=True)
            except PathSyntaxError as exc:
                raise AppError("MAPPING_INVALID_PATH", f"子记录路径非法: {child.record_path}: {exc}") from exc
        for field in record_type.fields:
            try:
                compile_path(field.path, relative=True)
            except PathSyntaxError as exc:
                raise AppError("MAPPING_INVALID_PATH", f"字段路径非法: {field.path}: {exc}") from exc

        if record_type.chunk_policy != "ignore":
            roles = {f.role for f in record_type.fields}
            if not ({"content", "title"} & roles):
                raise AppError(
                    "MAPPING_REQUIRES_TEXT",
                    f"记录类型 {record_type.name} 至少需要一个 content 或 title 字段",
                )


class MappingService:
    """模板/版本管理 + 文档绑定。所有写操作在事务内完成。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_id: int,
    ) -> None:
        self._factory = session_factory
        self._user_id = user_id

    async def create_template(self, definition: MappingDefinition) -> MappingTemplate:
        validate_definition(definition)
        async with self._factory() as session:
            template = MappingTemplate(
                name=definition.name or "未命名模板",
                source_format=definition.source_format,
                created_by_user_id=self._user_id,
            )
            session.add(template)
            await session.flush()

            version = MappingTemplateVersion(
                mapping_template_id=template.id,
                version=1,
                mapping_json=canonicalize_definition(definition),
                structure_fingerprint=definition_fingerprint(definition),
                created_by_user_id=self._user_id,
            )
            session.add(version)
            await session.commit()
            await session.refresh(template)
            return template

    async def create_version(
        self, template_id: int, definition: MappingDefinition
    ) -> MappingTemplateVersion:
        """保存定义；定义与最新版本结构一致时复用最新版本，否则创建新版本。"""
        validate_definition(definition)
        fingerprint = definition_fingerprint(definition)
        canonical = canonicalize_definition(definition)

        async with self._factory() as session:
            latest = await session.scalar(
                select(MappingTemplateVersion)
                .where(MappingTemplateVersion.mapping_template_id == template_id)
                .order_by(MappingTemplateVersion.version.desc())
            )
            if latest is not None and latest.structure_fingerprint == fingerprint:
                return latest

            next_version = (latest.version if latest is not None else 0) + 1
            version = MappingTemplateVersion(
                mapping_template_id=template_id,
                version=next_version,
                mapping_json=canonical,
                structure_fingerprint=fingerprint,
                created_by_user_id=self._user_id,
            )
            session.add(version)
            await session.commit()
            await session.refresh(version)
            return version

    async def update_version(
        self, template_id: int, version_id: int, definition: MappingDefinition
    ) -> MappingTemplateVersion:
        """就地更新版本；版本已被文档使用则拒绝（不可变）。"""
        async with self._factory() as session:
            version = await session.scalar(
                select(MappingTemplateVersion).where(
                    MappingTemplateVersion.id == version_id,
                    MappingTemplateVersion.mapping_template_id == template_id,
                )
            )
            if version is None:
                raise AppError("MAPPING_VERSION_NOT_FOUND", "映射版本不存在")

            used = await session.scalar(
                select(func.count(DocumentMapping.id)).where(
                    DocumentMapping.mapping_version_id == version_id
                )
            )
            if used:
                raise AppError(
                    "MAPPING_VERSION_IMMUTABLE",
                    "该映射版本已被文档使用，不可修改；请创建新版本",
                    status_code=409,
                )

            validate_definition(definition)
            version.mapping_json = canonicalize_definition(definition)
            version.structure_fingerprint = definition_fingerprint(definition)
            await session.commit()
            await session.refresh(version)
            return version

    async def bind_document(
        self,
        document_id: int,
        mapping_version_id: int,
        confirmed_by_user_id: int,
    ) -> DocumentMapping:
        """把文档绑定到某映射版本：同一事务内创建 DocumentMapping、
        置文档为 queued、创建 ingest 作业。任何一步失败都不产生副作用。"""
        async with self._factory() as session:
            doc = await session.scalar(
                select(Document).where(Document.id == document_id)
            )
            if doc is None:
                raise AppError("DOCUMENT_NOT_FOUND", "文档不存在", status_code=404)

            version = await session.scalar(
                select(MappingTemplateVersion).where(
                    MappingTemplateVersion.id == mapping_version_id
                )
            )
            if version is None:
                raise AppError("MAPPING_VERSION_NOT_FOUND", "映射版本不存在", status_code=404)

            # 绑定即确认：解析版本定义并完成最终校验，失败则不写任何东西
            try:
                definition = MappingDefinition.model_validate(json.loads(version.mapping_json))
            except ValueError as exc:
                raise AppError("MAPPING_INVALID_DEFINITION", f"版本定义损坏: {exc}") from exc
            validate_definition(definition)

            mapping = DocumentMapping(
                document_id=document_id,
                mapping_version_id=mapping_version_id,
                confirmed_by_user_id=confirmed_by_user_id,
                confirmed_at=datetime.now(UTC),
            )
            session.add(mapping)

            doc.status = "queued"
            session.add(
                DocumentJob(
                    doc_id=document_id,
                    owner_user_id=doc.owner_user_id,
                    job_type="ingest",
                    stage="queued",
                )
            )
            await session.commit()
            await session.refresh(mapping)
            return mapping
