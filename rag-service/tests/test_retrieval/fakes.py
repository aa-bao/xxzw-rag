from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from src.models.llm import ChatClient, ModelError
from src.retrieval.module import RetrievedChunk, RetrievalModule


class FakeRetrieval(RetrievalModule):
    def __init__(
        self,
        chunks: list[RetrievedChunk] | None = None,
        expanded_chunks: list[RetrievedChunk] | None = None,
    ) -> None:
        self._chunks = chunks or []
        self.expanded_chunks = expanded_chunks
        self.last_query: str = ""
        self.last_owner: int = -1
        self.last_kb: int = -1
        self.last_kb_ids: list[int] = []
        self.last_threshold: float | None = None
        self.last_expand_query: str = ""
        self.last_expand_owner: int = -1
        self.last_expand_core_ids: list[str] = []

    async def retrieve(
        self,
        query: str,
        owner_user_id: int,
        kb_id: int,
        top_k: int,
        similarity_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        self.last_query = query
        self.last_owner = owner_user_id
        self.last_kb = kb_id
        self.last_kb_ids.append(kb_id)
        self.last_threshold = similarity_threshold
        # 带 kb_id 的 chunk 只属于对应库；不带 kb_id 的 chunk（旧测试数据）兼容为全库返回
        chunks = [c for c in self._chunks if c.kb_id is None or c.kb_id == kb_id]
        chunks = chunks[:top_k]
        if similarity_threshold is not None:
            chunks = [c for c in chunks if c.score >= similarity_threshold]
        return chunks

    async def expand_context(
        self,
        query: str,
        owner_user_id: int,
        chunks: list[RetrievedChunk],
        *,
        seed_count: int = 2,
        max_chunks: int = 8,
        max_tokens: int = 2000,
    ) -> list[RetrievedChunk]:
        self.last_expand_query = query
        self.last_expand_owner = owner_user_id
        self.last_expand_core_ids = [chunk.chunk_id for chunk in chunks]
        if self.expanded_chunks is not None:
            return self.expanded_chunks
        return await super().expand_context(
            query,
            owner_user_id,
            chunks,
            seed_count=seed_count,
            max_chunks=max_chunks,
            max_tokens=max_tokens,
        )


class FakeChatClient:
    def __init__(self, rewritten_question: str = "") -> None:
        self.last_messages: list[dict[str, str]] = []
        self.rewritten_question = rewritten_question
        self.last_complete_messages: list[dict[str, str]] = []

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.last_complete_messages = messages
        return self.rewritten_question

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.last_messages = messages
        yield "test answer"
