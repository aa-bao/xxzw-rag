from __future__ import annotations

import uuid
from typing import Any

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


async def test_update_doc_chunk_regenerates_embedding_and_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _EditableChromaCollection(
        {"ids": ["chunk-1"], "metadatas": [{"doc_id": 7, "page": 2}]}
    )
    relay = _RecordingRelay([0.1, 0.2])
    retrieval = ChromaRetrieval(object(), relay=relay)
    monkeypatch.setattr(
        retrieval,
        "_get_client",
        lambda: _SingleCollectionClient(collection),
    )

    updated = await retrieval.update_doc_chunk(
        "kb_1_v1", 7, "chunk-1", "Repaired content"
    )

    assert relay.calls == [["Repaired content"]]
    assert collection.update_calls == [
        {
            "ids": ["chunk-1"],
            "documents": ["Repaired content"],
            "embeddings": [[0.1, 0.2]],
        }
    ]
    assert updated == {
        "chunk_id": "chunk-1",
        "content": "Repaired content",
        "doc_id": 7,
        "page": 2,
        "score": None,
    }


async def test_update_doc_chunk_rejects_chunk_from_another_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _EditableChromaCollection(
        {"ids": ["chunk-1"], "metadatas": [{"doc_id": 99}]}
    )
    relay = _RecordingRelay([0.1, 0.2])
    retrieval = ChromaRetrieval(object(), relay=relay)
    monkeypatch.setattr(
        retrieval,
        "_get_client",
        lambda: _SingleCollectionClient(collection),
    )

    updated = await retrieval.update_doc_chunk("kb_1_v1", 7, "chunk-1", "text")

    assert updated is None
    assert relay.calls == []
    assert collection.update_calls == []


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


async def test_expand_context_only_expands_first_two_global_seeds(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "context", "kb_context_v1")
        collection = _ExpandableChromaCollection(
            {
                10: {
                    "ids": ["10:2:left", "10:3:seed", "10:4:right"],
                    "documents": ["left", "seed one", "right"],
                    "metadatas": [
                        {"doc_id": 10, "title": "one"},
                        {"doc_id": 10, "title": "one"},
                        {"doc_id": 10, "title": "one"},
                    ],
                    "embeddings": [[0.8, 0.6], [1.0, 0.0], [0.0, 1.0]],
                },
                20: {
                    "ids": [["20:6:left", "20:7:seed", "20:8:right"]],
                    "documents": [["left two", "seed two", "right two"]],
                    "metadatas": [[
                        {"doc_id": 20, "title": "two", "chunk_index": 6},
                        {"doc_id": 20, "title": "two", "chunk_index": 7},
                        {"doc_id": 20, "title": "two", "chunk_index": 8},
                    ]],
                    "embeddings": [[[0.0, 0.0], [1.0, 0.0], [-1.0, 0.0]]],
                },
            }
        )
        relay = _RecordingRelay([1.0, 0.0])
        module = ChromaRetrieval(factory, relay=relay)
        monkeypatch.setattr(module, "_get_client", lambda: _SingleCollectionClient(collection))
        cores = [
            _core("10:3:seed", 10, None, kb_id, 0.9),
            _core("20:7:seed", 20, 7, kb_id, 0.8),
            _core("30:1:core", 30, 1, kb_id, 0.7),
            _core("40:1:core", 40, 1, kb_id, 0.6),
            _core("50:1:core", 50, 1, kb_id, 0.5),
        ]

        expanded = await module.expand_context("question", owner_id, cores)

        assert relay.calls == [["question"]]
        assert collection.get_calls == [10, 20]
        assert [chunk.chunk_id for chunk in expanded] == [
            "10:2:left",
            "10:3:seed",
            "10:4:right",
            "20:6:left",
            "20:7:seed",
            "30:1:core",
            "40:1:core",
            "50:1:core",
        ]
        assert len({chunk.chunk_id for chunk in expanded}) == len(expanded)
        left = next(chunk for chunk in expanded if chunk.chunk_id == "10:2:left")
        right = next(chunk for chunk in expanded if chunk.chunk_id == "10:4:right")
        assert left.score == pytest.approx(0.8)
        assert right.score == pytest.approx(0.0)
        zero = next(chunk for chunk in expanded if chunk.chunk_id == "20:6:left")
        assert zero.score == pytest.approx(0.0)
        assert left.is_neighbor is True
        assert right.is_neighbor is True
        first_core = next(chunk for chunk in expanded if chunk.chunk_id == "10:3:seed")
        assert first_core.is_neighbor is False
        assert first_core.rank_score == pytest.approx(0.9)
    finally:
        await engine.dispose()


async def test_expand_context_without_relay_degrades_to_packed_cores() -> None:
    module = ChromaRetrieval(session_factory=object())
    cores = [
        _core("10:3:seed", 10, 3, 1, 0.9),
        _core("20:7:seed", 20, 7, 1, 0.8),
    ]

    expanded = await module.expand_context("question", 1, cores)

    assert expanded == cores


async def test_expand_context_orders_hard_split_fragments_by_offset(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "fragments", "kb_fragments_v1")
        ids = [
            "10:3:hash#500",
            "10:4:right",
            "10:3:hash#1000",
            "10:2:left",
            "10:3:hash#0",
        ]
        collection = _ExpandableChromaCollection(
            {
                10: {
                    "ids": ids,
                    "documents": ids,
                    "metadatas": [
                        {"doc_id": 10, "title": "doc", "chunk_index": index}
                        for index in [3, 4, 3, 2, 3]
                    ],
                    "embeddings": [[1.0, 0.0] for _ in ids],
                }
            }
        )
        module = ChromaRetrieval(factory, relay=_RecordingRelay([1.0, 0.0]))
        monkeypatch.setattr(module, "_get_client", lambda: _SingleCollectionClient(collection))
        core = _core("10:3:hash#500", 10, 3, kb_id, 0.9)

        expanded = await module.expand_context(
            "question", owner_id, [core], seed_count=1
        )

        assert [chunk.chunk_id for chunk in expanded] == [
            "10:2:left",
            "10:3:hash#0",
            "10:3:hash#500",
            "10:3:hash#1000",
            "10:4:right",
        ]
        assert next(
            chunk for chunk in expanded if chunk.chunk_id == core.chunk_id
        ).is_neighbor is False
    finally:
        await engine.dispose()


async def test_expand_context_reserves_budget_for_core_before_fragmented_neighbor(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "budget", "kb_budget_v1")
        left_ids = [f"10:2:left#{offset}" for offset in range(0, 4000, 500)]
        ids = [*left_ids, "10:3:seed", "10:7:seed"]
        collection = _ExpandableChromaCollection(
            {
                10: {
                    "ids": ids,
                    "documents": ids,
                    "metadatas": [
                        {"doc_id": 10, "title": "doc", "chunk_index": index}
                        for index in [*([2] * len(left_ids)), 3, 7]
                    ],
                    "embeddings": [[1.0, 0.0] for _ in ids],
                }
            }
        )
        module = ChromaRetrieval(factory, relay=_RecordingRelay([1.0, 0.0]))
        monkeypatch.setattr(module, "_get_client", lambda: _SingleCollectionClient(collection))
        cores = [
            _core("10:3:seed", 10, 3, kb_id, 0.9),
            _core("10:7:seed", 10, 7, kb_id, 0.8),
        ]

        expanded = await module.expand_context("question", owner_id, cores)

        assert len(expanded) == 8
        assert {core.chunk_id for core in cores}.issubset(
            {chunk.chunk_id for chunk in expanded}
        )
        assert all(
            next(
                chunk for chunk in expanded if chunk.chunk_id == core.chunk_id
            ).is_neighbor
            is False
            for core in cores
        )
    finally:
        await engine.dispose()


async def test_expand_context_groups_same_document_seeds_in_one_filtered_get(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "grouped", "kb_grouped_v1")
        result = _window_result(10, [2, 3, 4, 6, 7, 8])
        collection = _FilteredExpandableCollection(result, result)
        module = ChromaRetrieval(factory, relay=_RecordingRelay([1.0, 0.0]))
        monkeypatch.setattr(module, "_get_client", lambda: _SingleCollectionClient(collection))
        cores = [
            _core("10:3:chunk", 10, 3, kb_id, 0.9),
            _core("10:7:chunk", 10, 7, kb_id, 0.8),
        ]

        expanded = await module.expand_context("question", owner_id, cores)

        assert collection.where_calls == [
            {
                "$and": [
                    {"doc_id": {"$eq": 10}},
                    {"chunk_index": {"$in": [2, 3, 4, 6, 7, 8]}},
                ]
            }
        ]
        assert {core.chunk_id for core in cores}.issubset(
            {chunk.chunk_id for chunk in expanded}
        )
    finally:
        await engine.dispose()


async def test_expand_context_falls_back_once_when_old_index_fast_read_is_empty(
    migrated_mysql_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        owner_id, kb_id = await _seed_kb(factory, "legacy", "kb_legacy_v1")
        legacy_result = {
            "ids": ["10:2:left", "10:3:seed", "10:4:right"],
            "documents": ["left", "seed", "right"],
            "metadatas": [
                {"doc_id": 10, "title": "legacy"},
                {"doc_id": 10, "title": "legacy"},
                {"doc_id": 10, "title": "legacy"},
            ],
            "embeddings": [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        }
        collection = _FilteredExpandableCollection(
            {"ids": [], "documents": [], "metadatas": [], "embeddings": []},
            legacy_result,
        )
        module = ChromaRetrieval(factory, relay=_RecordingRelay([1.0, 0.0]))
        monkeypatch.setattr(module, "_get_client", lambda: _SingleCollectionClient(collection))
        core = _core("10:3:seed", 10, None, kb_id, 0.9)

        expanded = await module.expand_context("question", owner_id, [core], seed_count=1)

        assert collection.where_calls == [
            {
                "$and": [
                    {"doc_id": {"$eq": 10}},
                    {"chunk_index": {"$in": [2, 3, 4]}},
                ]
            },
            {"doc_id": 10},
        ]
        assert [chunk.chunk_id for chunk in expanded] == [
            "10:2:left",
            "10:3:seed",
            "10:4:right",
        ]
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


class _EditableChromaCollection:
    def __init__(self, result: dict[str, object]) -> None:
        self._result = result
        self.update_calls: list[dict[str, object]] = []

    def get(self, *, ids: list[str], include: list[str]) -> dict[str, object]:
        assert include == ["metadatas"]
        return self._result if ids == self._result.get("ids") else {"ids": []}

    def update(self, **kwargs: object) -> None:
        self.update_calls.append(kwargs)


class _SingleCollectionClient:
    def __init__(self, collection: object) -> None:
        self._collection = collection

    def get_collection(self, name: str) -> object:
        return self._collection


class _ExpandableChromaCollection:
    def __init__(self, results_by_doc: dict[int, dict[str, object]]) -> None:
        self._results_by_doc = results_by_doc
        self.get_calls: list[int] = []

    def get(self, *, where: dict[str, Any], include: list[str]) -> dict[str, object]:
        assert include == ["documents", "metadatas", "embeddings"]
        if "$and" in where:
            doc_id = where["$and"][0]["doc_id"]["$eq"]
        else:
            doc_id = where["doc_id"]
        self.get_calls.append(doc_id)
        return self._results_by_doc[doc_id]


class _FilteredExpandableCollection:
    def __init__(
        self,
        filtered_result: dict[str, object],
        fallback_result: dict[str, object],
    ) -> None:
        self._filtered_result = filtered_result
        self._fallback_result = fallback_result
        self.where_calls: list[dict[str, object]] = []

    def get(
        self,
        *,
        where: dict[str, object],
        include: list[str],
    ) -> dict[str, object]:
        assert include == ["documents", "metadatas", "embeddings"]
        self.where_calls.append(where)
        return self._filtered_result if "$and" in where else self._fallback_result


class _RecordingRelay:
    def __init__(self, embedding: list[float]) -> None:
        self._embedding = embedding
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self._embedding]


def _core(
    chunk_id: str,
    doc_id: int,
    chunk_index: int | None,
    kb_id: int,
    rank_score: float,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        content=f"core {chunk_id}",
        doc_id=doc_id,
        title=f"doc {doc_id}",
        page=None,
        score=rank_score,
        kb_id=kb_id,
        kb_name="context",
        rank_score=rank_score,
        chunk_index=chunk_index,
    )


def _window_result(doc_id: int, indices: list[int]) -> dict[str, object]:
    ids = [f"{doc_id}:{index}:chunk" for index in indices]
    return {
        "ids": ids,
        "documents": ids,
        "metadatas": [
            {"doc_id": doc_id, "title": "doc", "chunk_index": index}
            for index in indices
        ],
        "embeddings": [[1.0, 0.0] for _ in indices],
    }


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
