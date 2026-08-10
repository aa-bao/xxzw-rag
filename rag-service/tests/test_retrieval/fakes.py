from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from src.models.llm import ChatClient, ModelError
from src.retrieval.module import RetrievedChunk, RetrievalModule


class FakeRetrieval(RetrievalModule):
    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self._chunks = chunks or []
        self.last_query: str = ""
        self.last_owner: int = -1
        self.last_kb: int = -1
        self.last_kb_ids: list[int] = []
        self.last_threshold: float | None = None

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


class FakeChatClient:
    def __init__(self) -> None:
        self.last_messages: list[dict[str, str]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.last_messages = messages
        yield "test answer"
