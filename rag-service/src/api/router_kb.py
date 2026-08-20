from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_permission
from src.db.models import (
    Conversation,
    ConversationKb,
    Document,
    DocumentJob,
    KnowledgeBase,
    MappingTemplate,
    MappingTemplateVersion,
    Message,
    QueryLog,
    Reference,
)
from src.db.repositories import KnowledgeBaseRepository
from src.db.scope import scope_condition
from src.ingestion.storage import atomic_save
from src.platform.principal import (
    PERMISSION_KB_MANAGE,
    PERMISSION_PROJECT_VIEW,
    ProjectPrincipal,
)
from src.retrieval.chroma import ChromaRetrieval
from src.shared.config import Settings
from src.shared.errors import AppError

router = APIRouter(prefix="/api/kb", tags=["knowledge_bases"])

# 软删知识库：标记 deleting 并移除出列表，硬删由异步 delete job 完成
_KB_DELETE_STATUS = "deleting"
# 重建索引：v2 起的 collection 以 cosine 空间创建（v1 为默认 L2 空间，需删除重建）
_KB_REBUILD_VERSION = 2
_KB_REBUILD_STATUS = "rebuilding"


class CreateKbRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1)
    description: str | None = None
    chunk_size: int = Field(default=256, ge=64, le=4096)
    overlap: int = Field(default=50, ge=0)


class UpdateKbRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    chunk_size: int | None = Field(default=None, ge=64, le=4096)
    overlap: int | None = Field(default=None, ge=0)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=50)


class TestKbRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)


class SetDefaultMappingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    mapping_version_id: int | None = None


def _serialize_kb(kb: KnowledgeBase, *, doc_count: int | None = None, chunk_total: int | None = None) -> dict[str, object]:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "chunk_size": kb.chunk_size,
        "overlap": kb.overlap,
        "embedding_model": kb.embedding_model,
        "embedding_dimension": kb.embedding_dimension,
        "active_collection": kb.active_collection,
        "index_version": kb.index_version,
        "index_status": kb.index_status,
        "similarity_threshold": float(kb.similarity_threshold) if kb.similarity_threshold is not None else None,
        "top_k": kb.top_k,
        "default_mapping_version_id": kb.default_mapping_version_id,
        "doc_count": doc_count,
        "chunk_total": chunk_total,
        # 封面图 URL（相对路径存在时拼接；?v=updated_at 破缓存，换封面后强制刷新）
        "cover_url": (
            f"/api/kb/{kb.id}/cover?v={kb.updated_at.strftime('%Y%m%d%H%M%S')}"
            if kb.cover_path and kb.updated_at
            else (f"/api/kb/{kb.id}/cover" if kb.cover_path else None)
        ),
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
    }


async def _ensure_embedding_dimension(request: Request) -> int:
    """缓存 embedding 维度；首次调用时向模型中转服务探测。"""
    embedding_dimension: int = getattr(request.app.state, "embedding_dimension", 0)
    if embedding_dimension == 0:
        relay_client = request.app.state.model_relay_client
        embedding_dimension = await relay_client.probe_dimension()
        request.app.state.embedding_dimension = embedding_dimension
    return embedding_dimension


@router.post("")
async def create_kb(
    body: CreateKbRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    repo = KnowledgeBaseRepository(db)

    embedding_dimension = await _ensure_embedding_dimension(request)

    kb = await repo.create(
        owner_user_id=principal.internal_user_id,
        tenant_id=principal.tenant_id or None,
        department_id=principal.department_id or None,
        name=body.name,
        description=body.description,
        embedding_model=request.app.state.runtime_relay.embedding_model,
        embedding_dimension=embedding_dimension,
        chunk_size=body.chunk_size,
        overlap=body.overlap,
    )
    return {"success": True, "data": _serialize_kb(kb)}


@router.get("")
async def list_kb(
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_PROJECT_VIEW)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """按租户与 dataScope 返回可见知识库，附文档数与 chunk 总数。"""
    repo = KnowledgeBaseRepository(db)
    kbs = await repo.list_all(scope_condition=scope_condition(KnowledgeBase, principal))

    kb_ids = [kb.id for kb in kbs]
    doc_count_by_kb: dict[int, int] = {}
    chunk_total_by_kb: dict[int, int] = {}
    if kb_ids:
        counts = (await db.execute(
            select(
                Document.kb_id,
                func.count(Document.id),
                func.coalesce(func.sum(Document.chunk_count), 0),
            )
            .where(Document.kb_id.in_(kb_ids), Document.status != "deleting")
            .group_by(Document.kb_id)
        )).all()
        doc_count_by_kb = {kb_id: doc_count for kb_id, doc_count, _ in counts}
        chunk_total_by_kb = {kb_id: int(chunk_total) for kb_id, _, chunk_total in counts}

    return {
        "success": True,
        "data": [
            _serialize_kb(
                kb,
                doc_count=doc_count_by_kb.get(kb.id, 0),
                chunk_total=chunk_total_by_kb.get(kb.id, 0),
            )
            for kb in kbs
        ],
    }


async def _get_kb_or_404(
    db: AsyncSession, kb_id: int, principal: ProjectPrincipal
) -> KnowledgeBase:
    kb = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            scope_condition(KnowledgeBase, principal),
        )
    )
    if kb is None or kb.enabled is False or kb.index_status == _KB_DELETE_STATUS:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)
    return kb


@router.get("/{kb_id}")
async def get_kb(
    kb_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    kb = await _get_kb_or_404(db, kb_id, principal)
    return {"success": True, "data": _serialize_kb(kb)}


@router.put("/{kb_id}")
async def update_kb(
    kb_id: int,
    body: UpdateKbRequest,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    kb = await _get_kb_or_404(db, kb_id, principal)
    updates: dict[str, object] = {
        key: value
        for key, value in {
            "name": body.name.strip() if body.name is not None else None,
            "description": body.description.strip() if body.description is not None else None,
            "chunk_size": body.chunk_size,
            "overlap": body.overlap,
            "similarity_threshold": Decimal(str(body.similarity_threshold)) if body.similarity_threshold is not None else None,
            "top_k": body.top_k,
        }.items()
        if value is not None
    }
    if not updates:
        raise AppError("NO_UPDATE_FIELDS", "没有需要更新的字段", status_code=400)
    for field, value in updates.items():
        setattr(kb, field, value)
    await db.commit()
    await db.refresh(kb)
    return {"success": True, "data": _serialize_kb(kb)}


@router.put("/{kb_id}/default-mapping")
async def set_default_mapping(
    kb_id: int,
    body: SetDefaultMappingRequest,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """设置知识库默认 JSON/JSONL 映射模板版本；传 null 清除默认模板。"""
    kb = await _get_kb_or_404(db, kb_id, principal)

    if body.mapping_version_id is not None:
        version = await db.scalar(
            select(MappingTemplateVersion)
            .join(
                MappingTemplate,
                MappingTemplate.id == MappingTemplateVersion.mapping_template_id,
            )
            .where(
                MappingTemplateVersion.id == body.mapping_version_id,
                MappingTemplate.created_by_user_id == principal.internal_user_id,
                scope_condition(MappingTemplate, principal),
            )
        )
        if version is None:
            raise AppError("MAPPING_VERSION_NOT_FOUND", "映射模板版本不存在", status_code=404)

    kb.default_mapping_version_id = body.mapping_version_id
    await db.commit()
    await db.refresh(kb)
    return {"success": True, "data": _serialize_kb(kb)}


@router.post("/{kb_id}/cover")
async def upload_kb_cover(
    kb_id: int,
    request: Request,
    file: UploadFile,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """上传知识库封面图（jpg/png/webp），保存到 uploads/ 并记录 cover_path。"""
    kb = await _get_kb_or_404(db, kb_id, principal)
    settings: Settings = request.app.state.settings

    filename = file.filename or "cover.png"
    ext = Path(filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise AppError("COVER_TYPE_UNSUPPORTED", "封面仅支持 jpg/png/webp/gif 图片", status_code=400)

    data = await file.read()
    rel_path = atomic_save(
        Path(settings.upload.root_dir),
        Path(settings.upload.temp_dir),
        data,
        f"cover{ext}",
        max_size_bytes=5 * 1024 * 1024,
    )
    kb.cover_path = rel_path
    # 显式更新 updated_at：server_onupdate 会被 ORM 写回的旧值覆盖
    kb.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(kb)
    return {"success": True, "data": _serialize_kb(kb)}


@router.get("/{kb_id}/cover")
async def get_kb_cover(
    kb_id: int,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_PROJECT_VIEW)),
    db: AsyncSession = Depends(_session_factory),
) -> Response:
    """读取知识库封面图片（进入应用的用户可见）。"""
    kb = await _get_kb_or_404(db, kb_id, principal)
    if not kb.cover_path:
        raise AppError("COVER_NOT_FOUND", "知识库未设置封面", status_code=404)
    path = Path(request.app.state.settings.upload.root_dir) / kb.cover_path
    if not path.exists():
        raise AppError("COVER_NOT_FOUND", "封面文件不存在", status_code=404)
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")
    return Response(content=path.read_bytes(), media_type=media_type)


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """删除知识库：级联删除文档与会话，索引标记 deleting 等待异步回收。"""
    kb = await _get_kb_or_404(db, kb_id, principal)

    # 1. 删该库下的消息引用（用新 kb_id 快照列直删，不经过会话子查询）
    await db.execute(sa_delete(Reference).where(
        Reference.kb_id == kb_id,
        Reference.message_id.in_(
            select(Message.id).join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.owner_user_id == kb.owner_user_id)
        ),
    ))
    # 2. 删查询日志（复合 FK 级联）
    await db.execute(sa_delete(QueryLog).where(
        QueryLog.kb_id == kb_id, QueryLog.owner_user_id == kb.owner_user_id
    ))
    # 3. 删会话-知识库关联行
    await db.execute(sa_delete(ConversationKb).where(
        ConversationKb.kb_id == kb_id, ConversationKb.owner_user_id == kb.owner_user_id
    ))
    # 4. 删孤儿会话（不再绑定任何知识库）；Message/Reference/QueryLog 由 DB CASCADE 清掉
    await db.execute(sa_delete(Conversation).where(
        Conversation.owner_user_id == kb.owner_user_id,
        ~exists(select(1).where(ConversationKb.conversation_id == Conversation.id)),
    ))
    # 5. 删文档及其 job（现有逻辑不动）
    await db.execute(sa_delete(DocumentJob).where(
        DocumentJob.doc_id.in_(
            select(Document.id).where(
                Document.kb_id == kb_id, Document.owner_user_id == kb.owner_user_id
            )
        )
    ))
    await db.execute(sa_delete(Document).where(
        Document.kb_id == kb_id, Document.owner_user_id == kb.owner_user_id
    ))
    kb.index_status = _KB_DELETE_STATUS
    kb.enabled = False
    await db.commit()
    return {"success": True, "data": None}


@router.post("/{kb_id}/reindex")
async def reindex_kb(
    kb_id: int,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """重建知识库索引：删除旧 collection，v2 起以 cosine 空间重建。

    旧 collection（kb_{id}_v1）的空间类型在创建时固定，无法原地切换，
    只能删除后由 ingest worker 在消费新 job 时以 cosine 空间重新创建。
    重建期间 index_status 标记 rebuilding，检索暂时为空属预期行为。
    """
    kb = await _get_kb_or_404(db, kb_id, principal)

    # 1. 删除当前 collection（chromadb 不可用/不存在时静默跳过）
    chroma = getattr(request.app.state, "ingest_chroma", None)
    if chroma is not None:
        await chroma.delete_collection(kb.active_collection)

    # 2. 切换到 v2 collection，标记重建中；新 collection 由 ingest job 以 cosine 空间创建
    new_collection = f"kb_{kb_id}_v{_KB_REBUILD_VERSION}"
    kb.active_collection = new_collection
    kb.index_version = _KB_REBUILD_VERSION
    kb.index_status = _KB_REBUILD_STATUS
    await db.commit()

    # 3. 为非 deleting 文档各建一个 ingest job（参考单文档重建 reindex_doc）
    docs = (await db.execute(
        select(Document).where(
            Document.kb_id == kb_id,
            Document.owner_user_id == kb.owner_user_id,
            Document.status != _KB_DELETE_STATUS,
        )
    )).scalars().all()
    for doc in docs:
        doc.status = "pending"
        doc.chunk_count = 0
        doc.error_message = None
        doc.ingested_at = None
        db.add(DocumentJob(
            doc_id=doc.id,
            owner_user_id=doc.owner_user_id,
            tenant_id=doc.tenant_id,
            department_id=doc.department_id,
            job_type="ingest",
        ))
    await db.commit()

    return {"success": True, "data": {"job_count": len(docs)}}


@router.post("/{kb_id}/test")
async def test_kb(
    kb_id: int,
    body: TestKbRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """检索测试：从向量库检索 top_k 个 chunk 并返回。"""
    kb = await _get_kb_or_404(db, kb_id, principal)

    settings: Settings = request.app.state.settings
    top_k = body.top_k or settings.rag.top_k

    # 复用 worker 的 Chroma 实例（同一 persist 路径），保证能读到已入库的 chunk
    retrieval = getattr(request.app.state, "ingest_chroma", None)
    if retrieval is None:
        retrieval = ChromaRetrieval(
            request.app.state.session_factory,
            persist_dir=str(settings.rag.chroma_persist_dir),
            mode=settings.rag.chroma_mode,
        )
    try:
        chunks = await retrieval.retrieve(
            body.question,
            owner_user_id=kb.owner_user_id,
            kb_id=kb.id,
            top_k=top_k,
            similarity_threshold=body.similarity_threshold,
        )
    except Exception:
        chunks = []

    return {
        "success": True,
        "data": [
            {
                "chunk_id": c.chunk_id,
                "content": c.content,
                "doc_id": c.doc_id,
                "title": c.title,
                "page": c.page,
                "score": c.score,
            }
            for c in chunks
        ],
    }
