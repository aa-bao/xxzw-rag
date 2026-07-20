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

    async def retrieve(
        self, query: str, owner_user_id: int, kb_id: int, top_k: int
    ) -> list[RetrievedChunk]:
        self.last_query = query
        self.last_owner = owner_user_id
        self.last_kb = kb_id
        return self._chunks[:top_k]


class FakeChatClient:
    def __init__(self) -> None:
        self.last_messages: list[dict[str, str]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.last_messages = messages
        yield "test answer"
