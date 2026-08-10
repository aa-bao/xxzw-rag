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
    kb_id: int | None = None
    kb_name: str | None = None


class RetrievalModule:
    async def retrieve(
        self,
        query: str,
        owner_user_id: int,
        kb_id: int,
        top_k: int,
        similarity_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve top_k chunks, enforcing owner isolation via KB ownership check."""
        raise NotImplementedError

    async def retrieve_multi(
        self,
        query: str,
        owner_user_id: int,
        kb_ids: list[int],
        top_k: int,
        similarity_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        """对每个知识库分别检索，按 score 降序合并后整体截断 top_k。

        稳定排序：同分 chunk 保持各库检索结果内的原始顺序。
        """
        chunks: list[RetrievedChunk] = []
        for kb_id in kb_ids:
            chunks.extend(
                await self.retrieve(query, owner_user_id, kb_id, top_k, similarity_threshold)
            )
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[:top_k]
