from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_user
from src.db.repositories import KnowledgeBaseRepository
from src.shared.config import Settings

router = APIRouter(prefix="/api/kb", tags=["knowledge_bases"])


class CreateKbRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1)
    description: str | None = None
    chunk_size: int = Field(default=512, ge=64, le=4096)
    overlap: int = Field(default=50, ge=0)


@router.post("")
async def create_kb(
    body: CreateKbRequest,
    request: Request,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    repo = KnowledgeBaseRepository(db)
    kb = await repo.create(
        owner_user_id=user_id,
        name=body.name,
        description=body.description,
        embedding_model=settings.model_relay.embedding_model,
        embedding_dimension=128,  # placeholder — probe_client will be injected later
        chunk_size=body.chunk_size,
        overlap=body.overlap,
    )
    return {
        "success": True,
        "data": {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "chunk_size": kb.chunk_size,
            "overlap": kb.overlap,
            "embedding_model": kb.embedding_model,
            "embedding_dimension": kb.embedding_dimension,
            "active_collection": kb.active_collection,
            "index_version": kb.index_version,
        },
    }


@router.get("")
async def list_kb(
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    repo = KnowledgeBaseRepository(db)
    kbs = await repo.list_by_owner(user_id)
    return {
        "success": True,
        "data": [
            {
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
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
                "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
            }
            for kb in kbs
        ],
    }
