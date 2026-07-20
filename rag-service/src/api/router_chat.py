from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_user
from src.db.models import Conversation, KnowledgeBase
from src.db.repositories import KnowledgeBaseRepository
from src.shared.errors import AppError

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateConversationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    kb_id: int


class QueryRequest(BaseModel):
    model_config = {"extra": "forbid"}
    conversation_id: str
    question: str = Field(min_length=1)


@router.post("/conversations")
async def create_conversation(
    body: CreateConversationRequest,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_owned(body.kb_id, user_id)
    if kb is None:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)

    conv = Conversation(
        id=uuid.uuid4().hex,
        owner_user_id=user_id,
        kb_id=kb.id,
        title=None,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)

    return {
        "success": True,
        "data": {
            "id": conv.id,
            "kb_id": conv.kb_id,
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
        },
    }


@router.get("/history")
async def list_conversations(
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    result = await db.execute(
        select(Conversation).where(
            Conversation.owner_user_id == user_id,
        ).order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()
    return {
        "success": True,
        "data": [
            {
                "id": c.id,
                "kb_id": c.kb_id,
                "title": c.title,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in convs
        ],
    }


@router.post("/query")
async def query(
    body: QueryRequest,
    request: Request,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
):
    # Check conversation ownership
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.id == body.conversation_id,
            Conversation.owner_user_id == user_id,
        )
    )
    if conv is None:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)

    # Delegate to query engine (stub — uses simple response)
    from src.engine.query import QueryEngine

    async def sse_stream():
        yield f"event: chunk\ndata: {{\"content\": \"测试回答\"}}\n\n"
        yield f"event: done\ndata: {{\"completed\": true}}\n\n"

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
