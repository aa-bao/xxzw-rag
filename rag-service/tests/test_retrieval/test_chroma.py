from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import KnowledgeBase, User
from src.db.repositories import KnowledgeBaseRepository
from src.db.session import create_engine, create_session_factory
from src.retrieval.chroma import ChromaRetrieval
from src.retrieval.module import RetrievedChunk


async def test_retrieve_checks_owner_before_chroma(
    migrated_mysql_url: str,
) -> None:
    """Owner isolation: retrieval for another user's KB returns empty list."""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            owner = User(
                username=f"owner-{uuid.uuid4().hex[:8]}",
                password_hash="hash",
                role="user",
                status="active",
            )
            session.add(owner)
            await session.flush()

            kb = KnowledgeBase(
                owner_user_id=owner.id,
                name="other-kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_other_v1",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id
            owner_id = owner.id

        module = ChromaRetrieval(factory)
        result = await module.retrieve("secret", owner_user_id=owner_id + 1, kb_id=kb_id, top_k=5)

        assert result == []
    finally:
        await engine.dispose()
