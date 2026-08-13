from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_permission
from src.db.models import Document, DocumentJob, KnowledgeBase
from src.db.repositories import KnowledgeBaseRepository
from src.db.scope import scope_condition
from src.ingestion.docx import DocxParser
from src.ingestion.factory import ParserFactory
from src.ingestion.storage import atomic_save, atomic_save_upload
from src.ingestion.text import TxtParser
from src.platform.principal import PERMISSION_KB_MANAGE, PERMISSION_PROJECT_VIEW, ProjectPrincipal
from src.shared.config import Settings
from src.shared.errors import AppError

router = APIRouter(prefix="/api", tags=["documents"])

# Register known parsers at module load
_factory = ParserFactory()
_factory.register(".txt", "text/plain", TxtParser())
_factory.register(".md", "text/markdown", TxtParser())
_factory.register(".markdown", "text/markdown", TxtParser())
_factory.register(
    ".docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocxParser(),
)

# 软删文档状态：标记 deleting 后由异步 delete job 清理文件与向量
_DOC_DELETE_STATUS = "deleting"


class UpdateChunkRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)


async def _get_kb_for_docs(
    db: AsyncSession, kb_id: int, principal: ProjectPrincipal
) -> KnowledgeBase:
    """文档接口仅管理员可用；知识库必须存在且启用。"""
    kb = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            scope_condition(KnowledgeBase, principal),
        )
    )
    if kb is None or kb.enabled is False:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)
    return kb


async def _get_doc(
    db: AsyncSession, kb_id: int, doc_id: int, principal: ProjectPrincipal
) -> Document:
    doc = await db.scalar(
        select(Document).where(
            Document.id == doc_id,
            Document.kb_id == kb_id,
            scope_condition(Document, principal),
        )
    )
    if doc is None:
        raise AppError("DOC_NOT_FOUND", "文档不存在", status_code=404)
    return doc


def _serialize_doc(doc: Document) -> dict[str, object]:
    return {
        "id": doc.id,
        "title": doc.title,
        "source": doc.source,
        "source_type": doc.source_type,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "file_size_bytes": doc.file_size_bytes,
        "error_message": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "ingested_at": doc.ingested_at.isoformat() if doc.ingested_at else None,
    }


async def _upload_doc_to_kb(
    request: Request,
    db: AsyncSession,
    kb_id: int,
    kb: KnowledgeBase,
    file: UploadFile,
) -> tuple[Document, int | None]:
    settings: Settings = request.app.state.settings

    filename = file.filename or "untitled.txt"
    extension = Path(filename).suffix.lower()
    structured = extension in {".json", ".jsonl"}

    if structured:
        rel_path, file_size, content_hash = await atomic_save_upload(
            Path(settings.upload.root_dir),
            Path(settings.upload.temp_dir),
            file,
            filename,
            max_size_bytes=settings.upload.max_size_mb * 1024 * 1024,
        )
    else:
        data = await file.read()
        # 普通文档继续在持久化前验证解析器；结构化文件在 profile 阶段校验语法。
        _factory.parse(filename, file.content_type or "text/plain", data)
        content_hash = hashlib.sha256(data).hexdigest()
        file_size = len(data)
        rel_path = ""

    # 查重：同知识库内已存在相同内容的文档（按内容 sha256，不看文件名）
    dup = await db.scalar(
        select(Document).where(
            Document.kb_id == kb_id,
            Document.content_hash == content_hash,
            Document.status != _DOC_DELETE_STATUS,
        )
    )
    if dup is not None:
        if structured:
            (Path(settings.upload.root_dir) / rel_path).unlink(missing_ok=True)
        raise AppError(
            "DOC_DUPLICATE",
            f"该文件已存在（文档「{dup.title}」），请勿重复上传",
            status_code=409,
        )

    if not structured:
        rel_path = atomic_save(
            Path(settings.upload.root_dir),
            Path(settings.upload.temp_dir),
            data,
            filename,
            max_size_bytes=settings.upload.max_size_mb * 1024 * 1024,
        )

    doc = Document(
        kb_id=kb_id,
        owner_user_id=kb.owner_user_id,
        tenant_id=getattr(kb, "tenant_id", None),
        department_id=getattr(kb, "department_id", None),
        title=filename,
        source=filename,
        source_type="upload",
        file_path=rel_path,
        file_size_bytes=file_size,
        content_hash=content_hash,
        status="awaiting_mapping" if structured else "pending",
    )
    db.add(doc)
    await db.flush()

    job: DocumentJob | None = None
    if not structured:
        job = DocumentJob(
            doc_id=doc.id,
            owner_user_id=kb.owner_user_id,
            tenant_id=getattr(kb, "tenant_id", None),
            department_id=getattr(kb, "department_id", None),
            job_type="ingest",
        )
        db.add(job)
        await db.flush()
    await db.commit()
    await db.refresh(doc)
    if job is not None:
        await db.refresh(job)

    return doc, job.id if job is not None else None


@router.post("/kb/{kb_id}/docs/upload")
async def upload_doc(
    kb_id: int,
    request: Request,
    file: UploadFile,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    kb = await _get_kb_for_docs(db, kb_id, principal)
    doc, job_id = await _upload_doc_to_kb(request, db, kb_id, kb, file)
    data: dict[str, object] = {"doc_id": doc.id, "status": doc.status}
    if job_id is not None:
        data["job_id"] = job_id
    return {"success": True, "data": data}


@router.get("/kb/{kb_id}/docs")
async def list_docs(
    kb_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    await _get_kb_for_docs(db, kb_id, principal)
    result = await db.execute(
        select(Document)
        .where(
            Document.kb_id == kb_id,
            scope_condition(Document, principal),
        )
        .order_by(Document.created_at.desc())
    )
    docs = [d for d in result.scalars().all() if d.status != _DOC_DELETE_STATUS]
    return {
        "success": True,
        "data": [_serialize_doc(doc) for doc in docs],
    }


@router.get("/kb/{kb_id}/docs/{doc_id}/status")
async def doc_status(
    kb_id: int,
    doc_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    await _get_kb_for_docs(db, kb_id, principal)
    doc = await _get_doc(db, kb_id, doc_id, principal)

    stage: str | None = None
    job = await db.scalar(
        select(DocumentJob)
        .where(DocumentJob.doc_id == doc_id)
        .order_by(DocumentJob.created_at.desc())
        .limit(1)
    )
    if job is not None:
        stage = job.stage

    return {
        "success": True,
        "data": {
            "doc_id": doc.id,
            "status": doc.status,
            "stage": stage,
            "chunk_count": doc.chunk_count,
            "error_message": doc.error_message,
        },
    }


@router.delete("/kb/{kb_id}/docs/{doc_id}")
async def delete_doc(
    kb_id: int,
    doc_id: int,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """删除文档：软删（status=deleting）+ 同步清理向量库中的该文档分块。"""
    await _get_kb_for_docs(db, kb_id, principal)
    doc = await _get_doc(db, kb_id, doc_id, principal)
    if doc.status == _DOC_DELETE_STATUS:
        return {"success": True, "data": None}

    # 清理向量库中的分块（chromadb 不可用时静默跳过）
    kb = await _get_kb_for_docs(db, kb_id, principal)
    collection_name = kb.active_collection or f"kb_{kb_id}_v1"
    chroma = getattr(request.app.state, "ingest_chroma", None)
    if chroma is not None:
        await chroma.delete_doc_chunks(collection_name, doc_id)

    job = DocumentJob(
        doc_id=doc.id,
        owner_user_id=doc.owner_user_id,
        tenant_id=doc.tenant_id,
        department_id=doc.department_id,
        job_type="delete",
    )
    db.add(job)
    doc.status = _DOC_DELETE_STATUS
    doc.chunk_count = 0
    doc.error_message = None
    # 清空内容哈希：软删文档不再参与查重（否则同内容重传会被残留记录拦截）
    doc.content_hash = None
    await db.commit()
    return {"success": True, "data": None}


@router.post("/kb/{kb_id}/docs/{doc_id}/reindex")
async def reindex_doc(
    kb_id: int,
    doc_id: int,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """重建索引：删除旧 chunk 并新建 ingest job 重新入库。"""
    await _get_kb_for_docs(db, kb_id, principal)
    doc = await _get_doc(db, kb_id, doc_id, principal)

    # 清理旧 chunk（向量库 + 计数）与旧 job
    kb = await _get_kb_for_docs(db, kb_id, principal)
    collection_name = kb.active_collection or f"kb_{kb_id}_v1"
    chroma = getattr(request.app.state, "ingest_chroma", None)
    if chroma is not None:
        await chroma.delete_doc_chunks(collection_name, doc_id)
    await db.execute(sa_delete(DocumentJob).where(DocumentJob.doc_id == doc_id))

    doc.status = "pending"
    doc.chunk_count = 0
    doc.error_message = None
    doc.ingested_at = None

    job = DocumentJob(
        doc_id=doc.id,
        owner_user_id=doc.owner_user_id,
        tenant_id=doc.tenant_id,
        department_id=doc.department_id,
        job_type="ingest",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return {"success": True, "data": {"job_id": job.id}}


@router.get("/kb/{kb_id}/docs/{doc_id}/chunks")
async def list_doc_chunks(
    kb_id: int,
    doc_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """文档 chunk 列表（分页，读向量库）。"""
    await _get_kb_for_docs(db, kb_id, principal)
    await _get_doc(db, kb_id, doc_id, principal)

    kb = await _get_kb_for_docs(db, kb_id, principal)
    collection_name = kb.active_collection or f"kb_{kb_id}_v1"
    chroma = getattr(request.app.state, "ingest_chroma", None)
    if chroma is None:
        return {"success": True, "data": {"total": 0, "items": []}}

    items = await chroma.list_doc_chunks(
        collection_name,
        doc_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    if keyword:
        kw = keyword.lower()
        items = [it for it in items if kw in (it["content"] or "").lower()]
    return {"success": True, "data": {"total": len(items), "items": items}}


@router.put("/kb/{kb_id}/docs/{doc_id}/chunks/{chunk_id}")
async def update_doc_chunk(
    kb_id: int,
    doc_id: int,
    chunk_id: str,
    body: UpdateChunkRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """Update one chunk and regenerate its vector with the active embedding model."""
    kb = await _get_kb_for_docs(db, kb_id, principal)
    await _get_doc(db, kb_id, doc_id, principal)
    content = body.content.strip()
    if not content:
        raise AppError("CHUNK_CONTENT_REQUIRED", "切片内容不能为空")
    chroma = getattr(request.app.state, "ingest_chroma", None)
    if chroma is None:
        raise AppError("CHROMA_UNAVAILABLE", "向量库暂不可用", status_code=503)
    collection_name = kb.active_collection or f"kb_{kb_id}_v1"
    try:
        updated = await chroma.update_doc_chunk(
            collection_name,
            doc_id,
            chunk_id,
            content,
        )
    except Exception as exc:
        raise AppError("CHUNK_UPDATE_FAILED", f"切片保存失败: {exc}", status_code=502) from exc
    if updated is None:
        raise AppError("CHUNK_NOT_FOUND", "切片不存在或不属于该文档", status_code=404)
    return {"success": True, "data": updated}


@router.get("/kb/{kb_id}/docs/{doc_id}/raw")
async def get_doc_raw(
    kb_id: int,
    doc_id: int,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_KB_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    """读取文档原始文件内容（txt/md/docx 提取文本）。用于前端「原文 vs 切片」对比。"""
    await _get_kb_for_docs(db, kb_id, principal)
    doc = await _get_doc(db, kb_id, doc_id, principal)

    settings: Settings = request.app.state.settings
    path = Path(settings.upload.root_dir) / doc.file_path
    if not path.exists():
        raise AppError("DOC_FILE_MISSING", "原始文件不存在", status_code=404)

    ext = path.suffix.lower()
    if ext in (".txt", ".md", ".markdown", ".json", ".jsonl"):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise AppError("DOC_ENCODING_UNSUPPORTED", "文件编码不支持预览", status_code=400)
    elif ext == ".docx":
        parser = _factory.parse(doc.title, "application/octet-stream", path.read_bytes())
        content = "\n\n".join(section.text for section in parser)
    else:
        raise AppError("DOC_PREVIEW_UNSUPPORTED", "该文件类型暂不支持预览", status_code=400)

    return {
        "success": True,
        "data": {
            "doc_id": doc.id,
            "title": doc.title,
            "content": content,
            "file_size_bytes": doc.file_size_bytes,
        },
    }


# ---------------------------------------------------------------------------
# 旧路径兼容（/api/docs/*）——原有测试与旧前端引用，行为与子路径一致
# ---------------------------------------------------------------------------


@router.post("/docs/upload")
async def legacy_upload_doc(
    request: Request,
    file: UploadFile,
    kb_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_PROJECT_VIEW)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    # 旧路径保持原有归属校验：仅本人知识库可上传
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_owned(kb_id, principal.internal_user_id)
    if kb is None:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)
    doc, job_id = await _upload_doc_to_kb(request, db, kb_id, kb, file)
    data: dict[str, object] = {"doc_id": doc.id, "status": doc.status}
    if job_id is not None:
        data["job_id"] = job_id
    return {"success": True, "data": data}


@router.get("/docs/{doc_id}/status")
async def legacy_doc_status(
    doc_id: int,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_PROJECT_VIEW)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    doc = await db.scalar(
        select(Document).where(
            Document.id == doc_id,
            scope_condition(Document, principal),
        )
    )
    if doc is None:
        raise AppError("DOC_NOT_FOUND", "文档不存在", status_code=404)

    stage: str | None = None
    job = await db.scalar(
        select(DocumentJob)
        .where(DocumentJob.doc_id == doc_id)
        .order_by(DocumentJob.created_at.desc())
        .limit(1)
    )
    if job is not None:
        stage = job.stage

    return {
        "success": True,
        "data": {
            "doc_id": doc.id,
            "status": doc.status,
            "stage": stage,
            "chunk_count": doc.chunk_count,
            "error_message": doc.error_message,
        },
    }
