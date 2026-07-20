from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_user
from src.db.models import Document, DocumentJob, KnowledgeBase
from src.db.repositories import KnowledgeBaseRepository
from src.ingestion.factory import ParserFactory
from src.ingestion.storage import atomic_save
from src.ingestion.text import TxtParser
from src.shared.config import Settings
from src.shared.errors import AppError

router = APIRouter(prefix="/api/docs", tags=["documents"])

# Register known parsers at module load
_factory = ParserFactory()
_factory.register(".txt", "text/plain", TxtParser())


@router.post("/upload")
async def upload_doc(
    request: Request,
    file: UploadFile,
    kb_id: int,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings

    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_owned(kb_id, user_id)
    if kb is None:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)

    filename = file.filename or "untitled.txt"
    data = await file.read()

    # Validate via parser
    _factory.parse(filename, file.content_type or "text/plain", data)

    rel_path = atomic_save(
        Path(settings.upload.root_dir),
        Path(settings.upload.temp_dir),
        data,
        filename,
        max_size_bytes=settings.upload.max_size_mb * 1024 * 1024,
    )

    doc = Document(
        kb_id=kb_id,
        owner_user_id=user_id,
        title=filename,
        source=filename,
        source_type="upload",
        file_path=rel_path,
        file_size_bytes=len(data),
    )
    db.add(doc)
    await db.flush()

    job = DocumentJob(
        doc_id=doc.id,
        owner_user_id=user_id,
        job_type="ingest",
    )
    db.add(job)
    await db.flush()
    await db.commit()
    await db.refresh(doc)
    await db.refresh(job)

    return {
        "success": True,
        "data": {"doc_id": doc.id, "job_id": job.id, "status": "pending"},
    }


@router.get("/{doc_id}/status")
async def doc_status(
    doc_id: int,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    doc = await db.scalar(
        select(Document).where(Document.id == doc_id, Document.owner_user_id == user_id)
    )
    if doc is None:
        raise AppError("DOC_NOT_FOUND", "文档不存在", status_code=404)

    return {
        "success": True,
        "data": {
            "doc_id": doc.id,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "error_message": doc.error_message,
        },
    }
