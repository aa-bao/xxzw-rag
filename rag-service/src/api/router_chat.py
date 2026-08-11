from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_user
from src.db.models import Conversation, ConversationKb, KnowledgeBase, Message, QueryLog, Reference
from src.db.repositories import KnowledgeBaseRepository
from src.engine.query import QueryEngine
from src.models.client import ModelRelayClient
from src.models.llm import ChatClient
from src.retrieval.chroma import ChromaRetrieval
from src.shared.errors import AppError

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateConversationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    kb_ids: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, max_length=10)


class QueryRequest(BaseModel):
    model_config = {"extra": "forbid"}
    conversation_id: str
    question: str = Field(min_length=1)


class RenameConversationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    title: str = Field(min_length=1, max_length=500)


def _build_query_engine(request: Request) -> QueryEngine:
    """Build the QueryEngine from app state, sharing the model-relay client."""
    settings = request.app.state.settings
    relay: ModelRelayClient = request.app.state.model_relay_client
    # 复用 worker 的 Chroma 实例（同一 persist 路径），保证能读到已入库的 chunk
    retrieval = getattr(request.app.state, "ingest_chroma", None)
    if retrieval is None:
        retrieval = ChromaRetrieval(
            request.app.state.session_factory,
            persist_dir=str(settings.rag.chroma_persist_dir),
            mode=settings.rag.chroma_mode,
        )
    # 壳对象：ChatClient 内部只读 settings.model_relay.*，热更新无需重建
    chat_client = ChatClient(request.app.state.settings_shell, relay.client)
    return QueryEngine(
        retrieval,
        chat_client,
        max_history_tokens=settings.llm.max_history_tokens,
        empty_response=settings.empty_response,
    )


@router.post("/conversations")
async def create_conversation(
    body: CreateConversationRequest,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    if len(set(body.kb_ids)) != len(body.kb_ids):
        raise AppError("KB_DUPLICATED", "知识库重复", status_code=422)
    kb_repo = KnowledgeBaseRepository(db)
    owned = await kb_repo.get_owned_many(body.kb_ids, user_id)
    # 任一库非本人拥有则整体 404
    if len(owned) != len(set(body.kb_ids)):
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)

    conv = Conversation(
        id=uuid.uuid4().hex,
        owner_user_id=user_id,
        title=None,
    )
    db.add(conv)
    await db.flush()  # flush 后拿到 id，再写关联行
    db.add_all(
        [
            ConversationKb(conversation_id=conv.id, kb_id=kid, owner_user_id=user_id)
            for kid in body.kb_ids
        ]
    )
    await db.commit()
    await db.refresh(conv)

    return {
        "success": True,
        "data": {
            "id": conv.id,
            "kb_ids": body.kb_ids,
            "kb_id": body.kb_ids[0],  # 兼容旧字段
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
        },
    }


@router.get("/history")
async def list_conversations(
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.owner_user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()

    # 全部关联行按会话分组，一次查全部知识库名
    links = (
        await db.scalars(select(ConversationKb).where(ConversationKb.owner_user_id == user_id))
    ).all()
    kb_ids_by_conv: dict[str, list[int]] = {}
    for link in links:
        kb_ids_by_conv.setdefault(link.conversation_id, []).append(link.kb_id)
    all_kb_ids = {kb_id for ids in kb_ids_by_conv.values() for kb_id in ids}
    kb_names: dict[int, str] = {}
    if all_kb_ids:
        kbs = await db.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(all_kb_ids)))
        kb_names = {kb.id: kb.name for kb in kbs}

    entries: list[dict[str, object]] = []
    for c in convs:
        ids = kb_ids_by_conv.get(c.id, [])
        entries.append(
            {
                "id": c.id,
                "kb_ids": ids,
                "kb_names": [kb_names.get(i) for i in ids],
                # 兼容旧字段：首库作为主库
                "kb_id": ids[0] if ids else None,
                "kb_name": kb_names.get(ids[0]) if ids else None,
                "title": c.title,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        )

    return {
        "success": True,
        "data": entries,
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_user_id == user_id,
        )
    )
    if conv is None:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)

    # Left-over 'streaming' rows (e.g. interrupted answers) are marked failed
    messages = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
    ).all()
    await db.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.status == "streaming",
        )
        .values(status="failed")
    )
    await db.commit()

    completed = [m for m in messages if m.status == "completed"]
    assistant_ids = [m.id for m in completed if m.role == "assistant"]
    refs_by_message: dict[int, list[Reference]] = {}
    if assistant_ids:
        refs = (
            await db.scalars(
                select(Reference).where(Reference.message_id.in_(assistant_ids))
            )
        ).all()
        for r in refs:
            refs_by_message.setdefault(r.message_id, []).append(r)

    return {
        "success": True,
        "data": [
            {
                "role": m.role,
                "content": m.content or "",
                "status": m.status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "references": [
                    {
                        "chunk_id": r.chunk_id,
                        "doc_id": r.doc_id,
                        "title": r.doc_title,
                        "kb_id": r.kb_id,
                        "kb_name": r.kb_name,
                        "snippet": r.snippet,
                        "score": float(r.score) if r.score is not None else None,
                        "page": r.page,
                        "is_neighbor": False,
                    }
                    for r in refs_by_message.get(m.id, [])
                ],
            }
            for m in completed
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_user_id == user_id,
        )
    )
    if conv is None:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)

    # Messages/References/QueryLogs are removed by the DB-level CASCADE FKs
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await db.commit()
    return {"success": True, "data": {"id": conversation_id}}


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    user_id: int = Depends(require_user),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_user_id == user_id,
        )
    )
    if conv is None:
        raise AppError("CONV_NOT_FOUND", "对话不存在", status_code=404)

    title = body.title.strip()
    if not title:
        raise AppError("CONV_TITLE_BLANK", "对话标题不能为空", status_code=422)

    conv.title = title
    await db.commit()
    await db.refresh(conv)

    return {
        "success": True,
        "data": {
            "id": conv.id,
            "title": conv.title,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        },
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

    # Auto-title: first question of an untitled conversation becomes its title
    if conv.title is None:
        question = body.question.strip()
        title = question if len(question) <= 20 else question[:20] + "…"
        conv.title = title
        await db.commit()

    # 会话绑定的全部知识库；top_k/threshold 取首个库的配置
    kb_ids = (
        await db.scalars(
            select(ConversationKb.kb_id).where(ConversationKb.conversation_id == conv.id)
        )
    ).all()
    if not kb_ids:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)
    kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_ids[0]))
    if kb is None:
        raise AppError("KB_NOT_FOUND", "知识库不存在", status_code=404)
    top_k = kb.top_k

    engine = _build_query_engine(request)

    async def sse_stream():
        async for event in engine.run(
            db,
            conversation_id=body.conversation_id,
            question=body.question,
            user_id=user_id,
            kb_ids=kb_ids,
            top_k=top_k,
            similarity_threshold=float(kb.similarity_threshold),
        ):
            yield event

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
