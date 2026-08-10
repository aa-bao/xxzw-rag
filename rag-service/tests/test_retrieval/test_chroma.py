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


def test_chroma_result_maps_to_retrieved_chunks() -> None:
    """Chromadb query result → RetrievedChunk mapping (distances → score)."""
    from src.retrieval.chroma import ChromaRetrieval

    result = {
        "ids": [["c1", "c2"]],
        "documents": [["content one", "content two"]],
        "metadatas": [
            [
                {"doc_id": 3, "title": "doc a", "page": 1},
                {"doc_id": 4, "title": "doc b"},
            ]
        ],
        "distances": [[0.1, 0.35]],
    }
    chunks = ChromaRetrieval._to_chunks(result)

    assert len(chunks) == 2
    assert chunks[0].chunk_id == "c1"
    assert chunks[0].content == "content one"
    assert chunks[0].doc_id == 3
    assert chunks[0].title == "doc a"
    assert chunks[0].page == 1
    assert chunks[0].score == 0.9  # 1 - distance
    assert chunks[1].score == 0.65
    assert chunks[1].page is None
    assert chunks[1].doc_id == 4


def test_chroma_result_maps_empty_query() -> None:
    """An empty query result yields no chunks."""
    from src.retrieval.chroma import ChromaRetrieval

    assert ChromaRetrieval._to_chunks({"ids": [[]], "documents": [[]], "metadatas": [[]]}) == []


def test_get_collection_creates_with_cosine_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """New collections are pinned to cosine distance so score=1-distance is a similarity."""
    retrieval = ChromaRetrieval(session_factory=object())
    calls: dict[str, object] = {}

    def fake_get_or_create_collection(name: str, metadata: dict[str, str]) -> object:
        calls["name"] = name
        calls["metadata"] = metadata
        return object()

    client = _FakeChromaClient({})
    client.get_or_create_collection = fake_get_or_create_collection
    monkeypatch.setattr(retrieval, "_get_client", lambda: client)

    collection = retrieval._get_collection("kb_1_v1", create=True)

    assert collection is not None
    assert calls["name"] == "kb_1_v1"
    assert calls["metadata"] == {"hnsw:space": "cosine"}


async def test_retrieve_filters_chunks_below_similarity_threshold(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunks below the threshold are dropped; the rest keep Chroma's sort order."""
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
                name="kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_threshold_v1",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id
            owner_id = owner.id

        result = {
            "ids": [["c1", "c2", "c3"]],
            "documents": [["one", "two", "three"]],
            "metadatas": [
                [
                    {"doc_id": 1, "title": "a"},
                    {"doc_id": 1, "title": "a"},
                    {"doc_id": 1, "title": "a"},
                ]
            ],
            # scores: 0.9, 0.6, 0.1
            "distances": [[0.1, 0.4, 0.9]],
        }
        module = ChromaRetrieval(factory)
        monkeypatch.setattr(module, "_get_client", lambda: _FakeChromaClient(result))

        filtered = await module.retrieve(
            "query", owner_user_id=owner_id, kb_id=kb_id, top_k=5, similarity_threshold=0.5
        )
        kept = await module.retrieve(
            "query", owner_user_id=owner_id, kb_id=kb_id, top_k=5
        )

        assert [c.chunk_id for c in filtered] == ["c1", "c2"]
        assert [c.score for c in filtered] == [0.9, 0.6]
        # threshold=None disables filtering entirely
        assert [c.chunk_id for c in kept] == ["c1", "c2", "c3"]
    finally:
        await engine.dispose()


async def test_retrieve_overfetches_and_reranks_exact_question(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "faq", "kb_faq_v1")
        ids = [f"c{i}" for i in range(20)]
        documents = [f"无关候选内容{i}" for i in range(20)]
        distances = [0.70 + i * 0.007 for i in range(20)]
        ids[10] = "exact"
        documents[10] = "规则。半托管商家承责？答：由商家承担。"
        distances[10] = 0.77
        result = {
            "ids": [ids],
            "documents": [documents],
            "metadatas": [[{"doc_id": 1, "title": "FAQ"} for _ in ids]],
            "distances": [distances],
        }
        collection = _RecordingChromaCollection(result)
        module = ChromaRetrieval(factory)
        monkeypatch.setattr(
            module,
            "_get_client",
            lambda: _SingleCollectionClient(collection),
        )

        chunks = await module.retrieve(
            "半托管商家承责？",
            owner_user_id=owner_id,
            kb_id=kb_id,
            top_k=5,
        )

        assert collection.query_calls[0]["n_results"] == 20
        candidate = next(chunk for chunk in chunks if chunk.chunk_id == "exact")
        assert candidate.score == pytest.approx(0.23)
        assert candidate.rank_score == pytest.approx(0.4225)
    finally:
        await engine.dispose()


class _FakeChromaClient:
    """Minimal chromadb client double: records get_or_create, serves query results."""

    def __init__(self, query_result: dict[str, object], *, by_collection: dict[str, dict[str, object]] | None = None) -> None:
        self._query_result = query_result
        self._by_collection = by_collection or {}
        self.queried_collections: list[str] = []

    def get_collection(self, name: str) -> _FakeChromaCollection:
        self.queried_collections.append(name)
        result = self._by_collection.get(name, self._query_result)
        return _FakeChromaCollection(result)

    def get_or_create_collection(self, name: str, metadata: dict[str, str]) -> _FakeChromaCollection:
        return _FakeChromaCollection(self._query_result)


class _FakeChromaCollection:
    def __init__(self, query_result: dict[str, object]) -> None:
        self._query_result = query_result

    def query(self, **kwargs: object) -> dict[str, object]:
        return self._query_result


class _RecordingChromaCollection(_FakeChromaCollection):
    def __init__(self, query_result: dict[str, object]) -> None:
        super().__init__(query_result)
        self.query_calls: list[dict[str, object]] = []

    def query(self, **kwargs: object) -> dict[str, object]:
        self.query_calls.append(kwargs)
        return super().query(**kwargs)


class _SingleCollectionClient:
    def __init__(self, collection: _RecordingChromaCollection) -> None:
        self._collection = collection

    def get_collection(self, name: str) -> _RecordingChromaCollection:
        return self._collection


async def _seed_kb(factory, name: str, collection: str, *, owner_id: int | None = None) -> tuple[int, int]:
    """建一个用户与其知识库，返回 (owner_id, kb_id)。owner_id 可复用同一用户。"""
    async with factory() as session:
        if owner_id is None:
            owner = User(
                username=f"owner-{uuid.uuid4().hex[:8]}",
                password_hash="hash",
                role="user",
                status="active",
            )
            session.add(owner)
            await session.flush()
            owner_id = owner.id
        kb = KnowledgeBase(
            owner_user_id=owner_id,
            name=name,
            embedding_model="test",
            embedding_dimension=128,
            active_collection=collection,
        )
        session.add(kb)
        await session.commit()
        return owner_id, kb.id


async def test_retrieve_fills_kb_info(migrated_mysql_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """单库 retrieve 返回的 chunk 携带 kb_id/kb_name 来源快照。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "法规库", "kb_info_v1")
        result = {
            "ids": [["c1"]],
            "documents": [["one"]],
            "metadatas": [[{"doc_id": 1, "title": "a"}]],
            "distances": [[0.1]],
        }
        module = ChromaRetrieval(factory)
        monkeypatch.setattr(module, "_get_client", lambda: _FakeChromaClient(result))

        chunks = await module.retrieve("query", owner_user_id=owner_id, kb_id=kb_id, top_k=5)

        assert len(chunks) == 1
        assert chunks[0].kb_id == kb_id
        assert chunks[0].kb_name == "法规库"
    finally:
        await engine.dispose()


async def test_retrieve_multi_merges_and_truncates(
    migrated_mysql_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两库各返回 2 chunks，retrieve_multi 按 score 降序合并并整体截断 top_k。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, id_a = await _seed_kb(factory, "kb-a", "kb_a_v1")
        _, id_b = await _seed_kb(factory, "kb-b", "kb_b_v1", owner_id=owner_id)

        result_a = {
            "ids": [["a1", "a2"]],
            "documents": [["from a", "from a"]],
            "metadatas": [[{"doc_id": 1, "title": "a"}, {"doc_id": 1, "title": "a"}]],
            "distances": [[0.1, 0.3]],  # scores: 0.9, 0.7
        }
        result_b = {
            "ids": [["b1", "b2"]],
            "documents": [["from b", "from b"]],
            "metadatas": [[{"doc_id": 1, "title": "b"}, {"doc_id": 1, "title": "b"}]],
            "distances": [[0.05, 0.5]],  # scores: 0.95, 0.5
        }
        client = _FakeChromaClient(
            {},
            by_collection={"kb_a_v1": result_a, "kb_b_v1": result_b},
        )
        module = ChromaRetrieval(factory)
        monkeypatch.setattr(module, "_get_client", lambda: client)

        chunks = await module.retrieve_multi(
            "query", owner_user_id=owner_id, kb_ids=[id_a, id_b], top_k=3
        )

        # 按 score 降序截断 3 条，每 chunk 带来源快照
        assert [c.score for c in chunks] == [0.95, 0.9, 0.7]
        assert [c.chunk_id for c in chunks] == ["b1", "a1", "a2"]
        assert {c.kb_id for c in chunks} == {id_a, id_b}
        assert {c.kb_name for c in chunks} == {"kb-a", "kb-b"}
        # 两个库都被查询
        assert set(client.queried_collections) == {"kb_a_v1", "kb_b_v1"}
    finally:
        await engine.dispose()


async def test_retrieve_multi_owner_isolation(
    migrated_mysql_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库 A 属当前用户、库 B 属他人 → 结果只含 A 的 chunk。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, id_a = await _seed_kb(factory, "kb-a", "kb_a_v1")
        other_id, id_b = await _seed_kb(factory, "kb-b", "kb_b_v1")
        assert other_id != owner_id

        result = {
            "ids": [["a1"]],
            "documents": [["from a"]],
            "metadatas": [[{"doc_id": 1, "title": "a"}]],
            "distances": [[0.1]],
        }
        module = ChromaRetrieval(factory)
        monkeypatch.setattr(module, "_get_client", lambda: _FakeChromaClient(result))

        chunks = await module.retrieve_multi(
            "query", owner_user_id=owner_id, kb_ids=[id_a, id_b], top_k=5
        )

        assert [c.chunk_id for c in chunks] == ["a1"]
        assert chunks[0].kb_id == id_a
        assert chunks[0].kb_name == "kb-a"
    finally:
        await engine.dispose()
