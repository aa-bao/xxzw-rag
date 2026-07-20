from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import KnowledgeBase
from src.retrieval.module import RetrievedChunk


class ChromaRetrieval:
    """Stub Chroma retrieval that only checks owner via the KB table."""

    def __init__(self, session_factory: object) -> None:
        self._session_factory = session_factory

    async def retrieve(
        self, query: str, owner_user_id: int, kb_id: int, top_k: int
    ) -> list[RetrievedChunk]:
        factory = self._session_factory
        async with factory() as session:
            kb = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == kb_id,
                    KnowledgeBase.owner_user_id == owner_user_id,
                )
            )
        if kb is None:
            return []
        # Stub: Chroma integration deferred to later task
        return []
