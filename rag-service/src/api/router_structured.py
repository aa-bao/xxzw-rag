from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_permission
from src.db.models import (
    Document,
    DocumentJob,
    DocumentMapping,
    KnowledgeBase,
    MappingTemplate,
    MappingTemplateVersion,
)
from src.db.scope import scope_condition
from src.platform.principal import PERMISSION_KB_MANAGE, ProjectPrincipal
from src.shared.config import Settings
from src.shared.errors import AppError
from src.structured.models import (
    FieldCondition,
    FieldMapping,
    MappingDefinition,
    MappingIssue,
    MappingSuggestion,
    RecordTypeMapping,
    RelationRule,
)
from src.structured.mapping import MappingService, definition_fingerprint
from src.structured.paths import PathSyntaxError, compile_path
from src.structured.profiler import PathStats, SourceProfile, profile_source, schema_fingerprint
from src.structured.stream import SourceSyntaxError, iter_source
from src.structured.suggestions import suggest_mapping
from src.structured.transforms import MappingContext, map_record


router = APIRouter(prefix="/api", tags=["structured-json"])


class ProfileRequest(BaseModel):
    doc_id: int


class PreviewRequest(BaseModel):
    doc_id: int
    mapping: dict[str, Any]
    limit: int | None = None
    compatibility_only: bool = False


class MappingTemplateRequest(BaseModel):
    template_id: int | None = None
    name: str | None = None
    mapping: dict[str, Any]


class MappingTemplateRenameRequest(BaseModel):
    name: str


class IngestRequest(BaseModel):
    doc_id: int
    mapping_version_id: int


def _inferred_type(stats: PathStats) -> str:
    if len(stats.types) > 1:
        return "mixed"
    if stats.container == "array":
        return "array"
    if stats.container == "object":
        return "object"
    if stats.null_count and stats.null_count == stats.observed_count:
        return "null"
    # 当前 profiler 将标量统一记录为 scalar；样例仅用于展示，不据其猜测数值类型。
    return "string"


def _field_payload(path: str, stats: PathStats) -> dict[str, object]:
    return {
        "path": path,
        "inferred_type": _inferred_type(stats),
        "sample": stats.examples[0] if stats.examples else None,
    }


def _record_mapping_payload(mapping: RecordTypeMapping) -> dict[str, object]:
    relation = mapping.relations[0] if mapping.relations else None
    return {
        "name": mapping.name,
        "record_path": mapping.record_path,
        "fields": [
            {
                "path": field.path,
                "role": field.role,
                "name": field.name or field.path.rsplit(".", 1)[-1],
                "transforms": [{"name": name} for name in field.transforms],
                "required": any(condition.kind == "required" for condition in field.conditions),
            }
            for field in mapping.fields
        ],
        "children": [_record_mapping_payload(child) for child in mapping.children],
        "chunk_policy": mapping.chunk_policy,
        "relation_rule": (
            {
                "source": relation.source_field,
                "target": relation.target_field,
                "strategy": "nearest_previous_sibling",
            }
            if relation is not None
            else None
        ),
    }


def _definition_payload(suggestion: MappingDefinition) -> dict[str, object]:
    return {
        "source_format": suggestion.source_format,
        "record_types": [_record_mapping_payload(record) for record in suggestion.record_types],
    }


def _suggestion_payload(suggestion: MappingSuggestion) -> dict[str, object]:
    return _definition_payload(suggestion)


def _field_from_client(payload: dict[str, Any]) -> FieldMapping:
    transforms: list[str] = []
    for transform in payload.get("transforms", []):
        if isinstance(transform, str):
            transforms.append(transform)
            continue
        if not isinstance(transform, dict) or not isinstance(transform.get("name"), str):
            raise AppError("MAPPING_INVALID", "字段转换格式无效")
        if transform.get("args"):
            raise AppError("MAPPING_INVALID", f"转换 {transform['name']} 暂不支持参数")
        transforms.append(transform["name"])
    conditions = (FieldCondition(kind="required"),) if payload.get("required") else ()
    return FieldMapping(
        path=str(payload.get("path", "")),
        role=payload.get("role", "display"),
        name=payload.get("name"),
        transforms=tuple(transforms),
        conditions=conditions,
    )


def _record_from_client(payload: dict[str, Any]) -> RecordTypeMapping:
    relation_payload = payload.get("relation_rule")
    relations: tuple[RelationRule, ...] = ()
    if isinstance(relation_payload, dict):
        relations = (
            RelationRule(
                source_field=str(relation_payload.get("source", "")),
                target_field=str(relation_payload.get("target", "")),
            ),
        )
    return RecordTypeMapping(
        name=str(payload.get("name", "record")),
        record_path=str(payload.get("record_path", "$")),
        fields=tuple(_field_from_client(field) for field in payload.get("fields", [])),
        children=tuple(_record_from_client(child) for child in payload.get("children", [])),
        relations=relations,
        chunk_policy=payload.get("chunk_policy", "semantic"),
    )


def _mapping_from_client(payload: dict[str, Any]) -> MappingDefinition:
    try:
        return MappingDefinition(
            source_format=payload.get("source_format", "json"),
            record_types=tuple(
                _record_from_client(record) for record in payload.get("record_types", [])
            ),
        )
    except (TypeError, ValueError) as exc:
        raise AppError("MAPPING_INVALID", f"映射配置无效: {exc}") from exc


def _record_path_for_preview(source_path: Path, source_format: str, record_path: str):
    if source_format == "json" and record_path == "$":
        with source_path.open("rb") as handle:
            for byte in handle.read(64):
                char = chr(byte)
                if char.isspace():
                    continue
                if char == "[":
                    return compile_path("$[*]")
                break
    return compile_path(record_path)


def _preview_row(record: Any, issues: list[MappingIssue]) -> dict[str, object]:
    title = record.title or ""
    content = record.content or ""
    embedding_text = "\n\n".join(part for part in (title, content) if part)
    warnings = [
        {"message": issue.message, "source_pointer": issue.source_pointer}
        for issue in issues
        if issue.source_pointer == record.source_pointer
    ]
    return {
        "record_id": record.record_id,
        "parent_id": record.parent_id,
        "record_type": record.record_type,
        "title": title,
        "content": content,
        "embedding_text": embedding_text,
        "lexical": {
            "title": title,
            "keywords": list(record.keywords),
            "content": content,
        },
        "filters": record.filters,
        "timestamps": record.timestamps,
        "display": record.display,
        "raw": record.raw,
        "source_pointer": record.source_pointer,
        "warnings": warnings,
    }


def _build_preview(
    source_path: Path,
    source_format: str,
    mapping: MappingDefinition,
    source_hash: str,
    limit: int,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    issues: list[MappingIssue] = []
    total_rows = 0
    context = MappingContext(source_hash=source_hash, mapping_version_id=0, on_issue=issues.append)
    for record_mapping in mapping.record_types:
        compiled = _record_path_for_preview(
            source_path, source_format, record_mapping.record_path
        )
        for source_record in iter_source(source_path, source_format, compiled):
            for mapped in map_record(source_record, record_mapping, context):
                total_rows += 1
                if len(rows) < limit:
                    rows.append(_preview_row(mapped, issues))
    return {
        "rows": rows,
        "warnings": [issue.message for issue in issues if issue.kind == "record_error"],
        "total_rows": total_rows,
        "limit": limit,
    }


def _profile_payload(doc_id: int, profile: SourceProfile) -> dict[str, object]:
    suggestion = suggest_mapping(profile)
    samples = [
        sample
        for stats in profile.paths.values()
        for sample in stats.examples
    ][:5]
    fields = [
        _field_payload(path, stats)
        for path, stats in sorted(profile.paths.items())
        if stats.child_of is None and stats.child_array_count == 0
    ]
    return {
        "source_format": profile.source_format,
        "doc_id": doc_id,
        "candidates": [
            {
                "record_path": profile.record_path,
                "record_count_estimate": profile.record_count,
                "field_count": len(fields),
                "nested_array_count": sum(
                    1 for stats in profile.paths.values() if stats.child_array_count > 0
                ),
                "confidence": 1.0,
                "samples": samples,
                "fields": fields,
                "suggested_mapping": _suggestion_payload(suggestion),
            }
        ],
        "fingerprint": schema_fingerprint(profile),
        "total_records_estimate": profile.record_count,
        "sampled_records": min(profile.record_count, 1000),
        "warnings": [],
    }


async def _owned_structured_doc(
    db: AsyncSession, kb_id: int, doc_id: int, principal: ProjectPrincipal
) -> Document:
    doc = await db.scalar(
        select(Document)
        .join(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
        .where(
            Document.id == doc_id,
            Document.kb_id == kb_id,
            Document.owner_user_id == principal.internal_user_id,
            KnowledgeBase.owner_user_id == principal.internal_user_id,
            scope_condition(Document, principal),
            scope_condition(KnowledgeBase, principal),
        )
    )
    if doc is None:
        raise AppError("DOC_NOT_FOUND", "文档不存在", status_code=404)
    if Path(doc.title).suffix.lower() not in {".json", ".jsonl"}:
        raise AppError("DOC_NOT_STRUCTURED", "该文档不是 JSON/JSONL 文件")
    return doc


@router.post("/kb/{kb_id}/json/profile")
async def profile_json(
    kb_id: int,
    body: ProfileRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    doc = await _owned_structured_doc(db, kb_id, body.doc_id, principal)
    settings: Settings = request.app.state.settings
    upload_root = Path(settings.upload.root_dir).resolve()
    source_path = (upload_root / doc.file_path).resolve()
    if not source_path.is_relative_to(upload_root) or not source_path.is_file():
        raise AppError("DOC_FILE_MISSING", "原始文件不存在", status_code=404)

    source_format = "jsonl" if source_path.suffix.lower() == ".jsonl" else "json"
    doc.status = "profiling"
    doc.error_message = None
    await db.commit()
    try:
        profile = await asyncio.to_thread(profile_source, source_path, source_format)
    except SourceSyntaxError as exc:
        doc.status = "failed"
        doc.error_message = str(exc)
        await db.commit()
        raise AppError("JSON_SYNTAX_INVALID", str(exc)) from exc

    doc.status = "awaiting_mapping"
    await db.commit()
    return {"success": True, "data": _profile_payload(doc.id, profile)}


@router.post("/kb/{kb_id}/json/preview")
async def preview_json(
    kb_id: int,
    body: PreviewRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    doc = await _owned_structured_doc(db, kb_id, body.doc_id, principal)
    mapping = _mapping_from_client(body.mapping)
    if not mapping.record_types:
        raise AppError("MAPPING_INVALID", "映射至少需要一个记录类型")
    if mapping.source_format not in {"json", "jsonl"}:
        raise AppError("MAPPING_INVALID", "不支持的源格式")
    limit = 5 if body.limit is None else body.limit
    if limit < 1 or limit > 50:
        raise AppError("PREVIEW_LIMIT_INVALID", "预览条数必须在 1 到 50 之间")

    settings: Settings = request.app.state.settings
    upload_root = Path(settings.upload.root_dir).resolve()
    source_path = (upload_root / doc.file_path).resolve()
    if not source_path.is_relative_to(upload_root) or not source_path.is_file():
        raise AppError("DOC_FILE_MISSING", "原始文件不存在", status_code=404)

    if body.compatibility_only:
        return {
            "success": True,
            "data": {
                "kind": "compatible",
                "added_optional_fields": [],
                "removed_paths": [],
                "changed_paths": [],
                "detail": None,
            },
        }

    doc.status = "previewing"
    doc.error_message = None
    await db.commit()
    try:
        preview = await asyncio.to_thread(
            _build_preview,
            source_path,
            mapping.source_format,
            mapping,
            doc.content_hash or "",
            limit,
        )
    except (SourceSyntaxError, PathSyntaxError) as exc:
        doc.status = "awaiting_mapping"
        doc.error_message = str(exc)
        await db.commit()
        raise AppError("JSON_PREVIEW_FAILED", str(exc)) from exc

    doc.status = "awaiting_mapping"
    await db.commit()
    return {"success": True, "data": preview}


async def _mapping_template_payload(db: AsyncSession, template: MappingTemplate) -> dict[str, object]:
    versions = (
        await db.execute(
            select(MappingTemplateVersion)
            .where(MappingTemplateVersion.mapping_template_id == template.id)
            .order_by(MappingTemplateVersion.version)
        )
    ).scalars().all()
    version_payload: list[dict[str, object]] = []
    total_usage = 0
    for version in versions:
        usage = int(
            await db.scalar(
                select(func.count(DocumentMapping.id)).where(
                    DocumentMapping.mapping_version_id == version.id
                )
            )
            or 0
        )
        total_usage += usage
        definition = MappingDefinition.model_validate(json.loads(version.mapping_json))
        version_payload.append(
            {
                "id": version.id,
                "version": version.version,
                "mapping": _definition_payload(definition),
                "fingerprint": version.structure_fingerprint,
                "usage_count": usage,
                "created_at": version.created_at.isoformat() if version.created_at else None,
                "created_by": str(version.created_by_user_id),
            }
        )
    return {
        "id": template.id,
        "name": template.name,
        "source_format": template.source_format,
        "current_version": versions[-1].version if versions else 0,
        "fingerprint": versions[-1].structure_fingerprint if versions else "",
        "usage_count": total_usage,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
        "versions": version_payload,
    }


@router.get("/mapping-templates")
async def list_mapping_templates(
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    templates = (
        await db.execute(
            select(MappingTemplate)
            .where(
                MappingTemplate.created_by_user_id == principal.internal_user_id,
                scope_condition(MappingTemplate, principal),
            )
            .order_by(MappingTemplate.updated_at.desc(), MappingTemplate.id.desc())
        )
    ).scalars().all()
    return {
        "success": True,
        "data": [await _mapping_template_payload(db, template) for template in templates],
    }


@router.get("/mapping-templates/{template_id}")
async def get_mapping_template(
    template_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    template = await db.scalar(
        select(MappingTemplate).where(
            MappingTemplate.id == template_id,
            MappingTemplate.created_by_user_id == principal.internal_user_id,
            scope_condition(MappingTemplate, principal),
        )
    )
    if template is None:
        raise AppError("MAPPING_TEMPLATE_NOT_FOUND", "映射模板不存在", status_code=404)
    return {"success": True, "data": await _mapping_template_payload(db, template)}


@router.post("/mapping-templates")
async def create_mapping_template_version(
    body: MappingTemplateRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    definition = _mapping_from_client(body.mapping)
    service = MappingService(
        request.app.state.session_factory,
        principal.internal_user_id,
        tenant_id=principal.tenant_id or None,
        department_id=principal.department_id or None,
    )
    if body.template_id is None:
        name = (body.name or "").strip()
        if not name:
            raise AppError("MAPPING_TEMPLATE_NAME_REQUIRED", "请输入映射模板名称")
        template = await service.create_template(definition.model_copy(update={"name": name}))
        response_template_id = int(template.id)
        await db.rollback()
        version = await db.scalar(
            select(MappingTemplateVersion)
            .where(MappingTemplateVersion.mapping_template_id == response_template_id)
            .order_by(MappingTemplateVersion.version.desc())
        )
    else:
        template = await db.scalar(
            select(MappingTemplate).where(
                MappingTemplate.id == body.template_id,
                MappingTemplate.created_by_user_id == principal.internal_user_id,
                scope_condition(MappingTemplate, principal),
            )
        )
        if template is None:
            raise AppError("MAPPING_TEMPLATE_NOT_FOUND", "映射模板不存在", status_code=404)
        response_template_id = int(template.id)
        version = await service.create_version(response_template_id, definition)
    if version is None:
        raise AppError("MAPPING_VERSION_CREATE_FAILED", "映射版本创建失败", status_code=500)
    return {
        "success": True,
        "data": {
            "template_id": response_template_id,
            "mapping_version_id": version.id,
            "version": version.version,
            "fingerprint": version.structure_fingerprint or definition_fingerprint(definition),
        },
    }


@router.put("/mapping-templates/{template_id}")
async def rename_mapping_template(
    template_id: int,
    body: MappingTemplateRenameRequest,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    template = await db.scalar(
        select(MappingTemplate).where(
            MappingTemplate.id == template_id,
            MappingTemplate.created_by_user_id == principal.internal_user_id,
            scope_condition(MappingTemplate, principal),
        )
    )
    if template is None:
        raise AppError("MAPPING_TEMPLATE_NOT_FOUND", "映射模板不存在", status_code=404)
    name = body.name.strip()
    if not name:
        raise AppError("MAPPING_TEMPLATE_NAME_REQUIRED", "请输入映射模板名称")
    template.name = name
    await db.commit()
    return {"success": True, "data": {"id": int(template.id), "name": template.name}}


@router.delete("/mapping-templates/{template_id}")
async def delete_mapping_template(
    template_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    template = await db.scalar(
        select(MappingTemplate).where(
            MappingTemplate.id == template_id,
            MappingTemplate.created_by_user_id == principal.internal_user_id,
            scope_condition(MappingTemplate, principal),
        )
    )
    if template is None:
        raise AppError("MAPPING_TEMPLATE_NOT_FOUND", "映射模板不存在", status_code=404)
    used_count = int(
        await db.scalar(
            select(func.count(DocumentMapping.id))
            .select_from(DocumentMapping)
            .join(
                MappingTemplateVersion,
                MappingTemplateVersion.id == DocumentMapping.mapping_version_id,
            )
            .where(MappingTemplateVersion.mapping_template_id == template.id)
        )
        or 0
    )
    if used_count > 0:
        raise AppError(
            "MAPPING_TEMPLATE_IN_USE",
            "该模板已被文档使用，不能删除；请保留模板或改用新模板后重试",
            status_code=409,
        )
    try:
        versions = (
            await db.execute(
                select(MappingTemplateVersion)
                .where(MappingTemplateVersion.mapping_template_id == template.id)
            )
        ).scalars().all()
        for version in versions:
            await db.delete(version)
        await db.delete(template)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise AppError(
            "MAPPING_TEMPLATE_DELETE_FAILED",
            f"删除模板失败：{exc}",
            status_code=400,
        ) from exc
    return {"success": True, "data": {"id": int(template.id)}}


@router.post("/kb/{kb_id}/json/ingest")
async def ingest_json(
    kb_id: int,
    body: IngestRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, Any]:
    doc = await _owned_structured_doc(db, kb_id, body.doc_id, principal)
    version = await db.scalar(
        select(MappingTemplateVersion)
        .join(MappingTemplate, MappingTemplate.id == MappingTemplateVersion.mapping_template_id)
        .where(
            MappingTemplateVersion.id == body.mapping_version_id,
            MappingTemplate.created_by_user_id == principal.internal_user_id,
            scope_condition(MappingTemplate, principal),
        )
    )
    if version is None:
        raise AppError("MAPPING_VERSION_NOT_FOUND", "映射版本不存在", status_code=404)
    existing = await db.scalar(
        select(DocumentMapping).where(DocumentMapping.document_id == doc.id)
    )
    if existing is not None:
        raise AppError("DOCUMENT_MAPPING_EXISTS", "该文档已确认映射，请勿重复提交", status_code=409)

    bound_doc_id = int(doc.id)
    bound_version_id = int(version.id)
    service = MappingService(
        request.app.state.session_factory,
        principal.internal_user_id,
        tenant_id=principal.tenant_id or None,
        department_id=principal.department_id or None,
    )
    await service.bind_document(bound_doc_id, bound_version_id, principal.internal_user_id)
    # 上面的绑定在独立事务提交；结束当前读取快照后再查询新建 job。
    await db.rollback()
    # The binding commits in a separate transaction. End this read snapshot
    # before looking up the newly-created job (important for MySQL RR).
    await db.rollback()
    job = await db.scalar(
        select(DocumentJob)
        .where(DocumentJob.doc_id == bound_doc_id, DocumentJob.job_type == "ingest")
        .order_by(DocumentJob.id.desc())
    )
    if job is None:
        raise AppError("INGEST_JOB_CREATE_FAILED", "入库任务创建失败", status_code=500)
    return {
        "success": True,
        "data": {
            "doc_id": bound_doc_id,
            "job_id": job.id,
            "mapping_version_id": bound_version_id,
            "status": "queued",
        },
    }
