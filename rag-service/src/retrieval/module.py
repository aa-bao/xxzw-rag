from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    content: str
    doc_id: int
    title: str
    page: int | None
    score: float


class RetrievalModule:
    async def retrieve(
        self,
        query: str,
        owner_user_id: int,
        kb_id: int,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Retrieve top_k chunks, enforcing owner isolation via KB ownership check."""
        raise NotImplementedError
