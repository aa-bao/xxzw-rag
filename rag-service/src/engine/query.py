from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, Message, Reference, QueryLog
from src.engine.history import truncate_history
from src.engine.prompt import build_messages, build_rewrite_messages
from src.models.llm import ChatClient, ModelError
from src.retrieval.module import RetrievedChunk, RetrievalModule
from src.shared.errors import AppError

SSE_CHUNK = "chunk"
SSE_REFERENCES = "references"
SSE_DONE = "done"
SSE_ERROR = "error"


def _sse_event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"


class QueryEngine:
    def __init__(
        self,
        retrieval: RetrievalModule,
        chat_client: ChatClient,
        max_history_tokens: int = 4096,
        empty_response: str = "没有找到相关信息。",
    ) -> None:
        self._retrieval = retrieval
        self._chat = chat_client
        self._max_history_tokens = max_history_tokens
        self._empty_response = empty_response

    async def run(
        self,
        db: AsyncSession,
        conversation_id: str,
        question: str,
        user_id: int,
        kb_ids: list[int],
        top_k: int,
        similarity_threshold: float | None = None,
    ) -> AsyncIterator[str]:
        # Load conversation and messages
        conv = await db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == user_id,
            )
        )
        if conv is None:
            yield _sse_event(SSE_ERROR, json.dumps({"code": "CONV_NOT_FOUND", "retryable": False}))
            return

        # Build history
        history_msgs = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.status == "completed",
            ).order_by(Message.sequence)
        )
        history_roles: list[dict[str, str]] = [
            {"role": m.role, "content": m.content or ""}
            for m in history_msgs.scalars().all()
        ]
        history_roles = truncate_history(history_roles, max_tokens=self._max_history_tokens)

        # Resolve pronouns and omitted context before retrieval. Rewriting is best-effort:
        # an unavailable model must not prevent the existing single-turn path from working.
        retrieval_question = question
        if history_roles:
            try:
                rewritten = await self._chat.complete(
                    build_rewrite_messages(question, history_roles)
                )
                if rewritten:
                    retrieval_question = rewritten
            except ModelError:
                pass

        # Save user message
        max_seq = await db.scalar(
            select(Message.sequence).where(
                Message.conversation_id == conversation_id
            ).order_by(Message.sequence.desc()).limit(1)
        )
        next_seq = (max_seq or -1) + 1

        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=question,
            status="completed",
            sequence=next_seq,
        )
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=None,
            status="streaming",
            sequence=next_seq + 1,
        )
        db.add_all([user_msg, assistant_msg])
        await db.flush()

        # Query log（kb_id 记首个库，与旧结构兼容）
        query_log = QueryLog(
            conversation_id=conversation_id,
            owner_user_id=user_id,
            kb_id=kb_ids[0],
            user_message_id=user_msg.id,
            assistant_message_id=assistant_msg.id,
        )
        db.add(query_log)

        # Retrieve：对每个知识库分别检索，合并后按 score 降序整体截断 top_k
        core_sources = await self._retrieval.retrieve_multi(
            retrieval_question, user_id, kb_ids, top_k, similarity_threshold
        )

        if not core_sources:
            assistant_msg.content = self._empty_response
            assistant_msg.status = "completed"
            assistant_msg.tokens_used = len(self._empty_response)
            query_log.result_status = "empty"
            await db.commit()
            yield _sse_event(SSE_CHUNK, json.dumps({"content": self._empty_response}))
            yield _sse_event(SSE_DONE, json.dumps({"completed": True}))
            return

        sources = await self._retrieval.expand_context(
            retrieval_question, user_id, core_sources
        )

        messages = build_messages(question, history_roles, sources)
        content_emitted = False
        full_content = ""

        try:
            async for chunk in self._chat.stream(messages):
                content_emitted = True
                full_content += chunk
                yield _sse_event(SSE_CHUNK, json.dumps({"content": chunk}))
        except ModelError as e:
            assistant_msg.status = "failed"
            assistant_msg.error_code = e.code
            query_log.result_status = "error"
            query_log.error_code = e.code
            await db.commit()
            yield _sse_event(
                SSE_ERROR, json.dumps({"code": e.code, "retryable": e.retryable})
            )
            return

        # Save references
        ref_ids = []
        for i, src in enumerate(sources):
            ref = Reference(
                message_id=assistant_msg.id,
                chunk_id=src.chunk_id,
                doc_id=src.doc_id,
                doc_title=src.title,
                kb_id=src.kb_id,
                kb_name=src.kb_name,
                snippet=src.content[:500],
                score=src.score,
                page=src.page,
            )
            db.add(ref)
            await db.flush()
            ref_ids.append(ref.id)

        assistant_msg.content = full_content
        assistant_msg.status = "completed"
        assistant_msg.tokens_used = len(full_content)
        query_log.result_status = "success"
        query_log.chunks_count = len(sources)
        await db.commit()

        yield _sse_event(
            SSE_REFERENCES,
            json.dumps(
                {
                    "items": [
                        {
                            "chunk_id": s.chunk_id,
                            "doc_id": s.doc_id,
                            "title": s.title,
                            "snippet": s.content[:500],
                            "score": s.score,
                            "page": s.page,
                            "kb_id": s.kb_id,
                            "kb_name": s.kb_name,
                            "is_neighbor": s.is_neighbor,
                        }
                        for s in sources
                    ]
                }
            ),
        )
        yield _sse_event(SSE_DONE, json.dumps({"completed": True}))
