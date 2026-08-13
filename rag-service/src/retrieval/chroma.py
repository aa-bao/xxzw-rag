from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import Any

from sqlalchemy import select

from src.db.models import KnowledgeBase
from src.retrieval.context import pack_context
from src.retrieval.module import RetrievedChunk, RetrievalModule
from src.retrieval.ranking import hybrid_score, lexical_score

logger = logging.getLogger(__name__)

try:  # pragma: no cover - chromadb is an optional dependency
    import chromadb
except ImportError:  # pragma: no cover
    chromadb = None  # type: ignore[assignment]


class ChromaRetrieval(RetrievalModule):
    """Chroma vector retrieval with KB-owner isolation.

    The collection is located via the KB's ``active_collection`` column (the
    naming scheme ``kb_{id}_v1`` is set at KB creation). chromadb is an
    optional dependency: when it is missing, the client cannot be opened, or
    the collection does not exist, retrieval degrades to an empty list with a
    logged warning — never raises.
    """

    def __init__(
        self,
        session_factory: object,
        *,
        persist_dir: str = "./data/chroma",
        mode: str = "persist",
        relay: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._persist_dir = persist_dir
        self._mode = mode
        self._relay = relay
        self._client: Any = None

    async def _query_embeddings(self, texts: list[str]) -> list[list[float]] | None:
        """用配置的 embedding 模型显式计算查询向量（与写入同一模型，维度一致）。

        relay 未注入（如纯测试场景）时返回 None，由调用方回退。
        """
        if self._relay is None:
            return None
        return await self._relay.embed(texts)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if chromadb is None:
            logger.warning("chromadb is not installed — ChromaRetrieval returns empty results")
            return None
        if self._mode != "persist":
            logger.warning("Chroma mode %r not supported yet — only 'persist' is wired", self._mode)
            return None
        try:
            self._client = chromadb.PersistentClient(path=self._persist_dir)
        except Exception as exc:
            logger.warning("Failed to open Chroma client: %s", exc)
            return None
        return self._client

    def _get_collection(self, collection_name: str, *, create: bool = False) -> Any:
        client = self._get_client()
        if client is None:
            return None
        try:
            if create:
                # 空间在创建时固定：余弦距离下 score=1-distance 才是相似度
                return client.get_or_create_collection(
                    name=collection_name, metadata={"hnsw:space": "cosine"}
                )
            return client.get_collection(name=collection_name)
        except Exception as exc:
            logger.warning("Chroma collection %r unavailable: %s", collection_name, exc)
            return None

    async def upsert_chunks(
        self,
        collection_name: str,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        """把分块与向量写入 collection。chromadb 不可用/失败时记 warning，不抛异常。"""
        collection = self._get_collection(collection_name, create=True)
        if collection is None:
            raise RuntimeError(f"Chroma collection {collection_name!r} unavailable")
        ids = [str(c["chunk_id"]) for c in chunks]
        documents = [c["content"] for c in chunks]
        metadatas = [
            {
                "doc_id": c.get("doc_id"),
                "title": c.get("title") or "",
                "page": c.get("page"),
                "chunk_index": c.get("chunk_index"),
                "record_id": c.get("record_id") or "",
                "parent_id": c.get("parent_id") or "",
                "record_type": c.get("record_type") or "",
                "source_pointer": c.get("source_pointer") or "",
                "mapping_version_id": c.get("mapping_version_id") or 0,
            }
            for c in chunks
        ]
        try:
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        except Exception as exc:
            logger.warning("Chroma upsert into %r failed: %s", collection_name, exc)
            raise

    async def delete_doc_chunks(self, collection_name: str, doc_id: int) -> None:
        """按 doc_id 删除 collection 中的分块。collection 不存在则静默跳过。"""
        collection = self._get_collection(collection_name, create=False)
        if collection is None:
            return
        try:
            collection.delete(where={"doc_id": doc_id})
        except Exception as exc:
            logger.warning("Chroma delete in %r failed: %s", collection_name, exc)

    async def delete_collection(self, collection_name: str) -> None:
        """删除整个 collection（幂等：collection 不存在时静默跳过）。

        用于重建索引：collection 的空间类型在创建时固定、无法原地修改，
        只能删除后由后续 ingest job 以新空间重建。
        """
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete_collection(name=collection_name)
        except Exception as exc:
            if chromadb is not None and isinstance(exc, chromadb.errors.NotFoundError):
                return  # 幂等：collection 不存在视为删除成功
            logger.warning("Chroma delete collection %r failed: %s", collection_name, exc)

    async def list_doc_chunks(
        self,
        collection_name: str,
        doc_id: int,
        *,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """列出某文档的分块（分页）。chromadb 不可用/无 collection 时返回空列表。"""
        collection = self._get_collection(collection_name, create=False)
        if collection is None:
            return []
        try:
            result = collection.get(
                where={"doc_id": doc_id},
                limit=limit,
                offset=offset,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning("Chroma get in %r failed: %s", collection_name, exc)
            return []
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        items: list[dict[str, Any]] = []
        for i, chunk_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            items.append(
                {
                    "chunk_id": str(chunk_id),
                    "content": documents[i] if i < len(documents) and documents[i] is not None else "",
                    "doc_id": _as_int(meta.get("doc_id")),
                    "page": _as_int(meta["page"]) if meta.get("page") is not None else None,
                    "score": None,
                }
            )
        return items

    async def update_doc_chunk(
        self,
        collection_name: str,
        doc_id: int,
        chunk_id: str,
        content: str,
    ) -> dict[str, Any] | None:
        """Update one owned document chunk and regenerate its embedding."""
        collection = self._get_collection(collection_name, create=False)
        if collection is None:
            return None
        result = collection.get(ids=[chunk_id], include=["metadatas"])
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        if not ids:
            return None
        metadata = metadatas[0] if metadatas and metadatas[0] else {}
        if _as_int(metadata.get("doc_id")) != doc_id:
            return None
        if self._relay is None:
            raise RuntimeError("Embedding service is unavailable")
        embeddings = await self._relay.embed([content])
        collection.update(
            ids=[chunk_id],
            documents=[content],
            embeddings=embeddings,
        )
        return {
            "chunk_id": chunk_id,
            "content": content,
            "doc_id": doc_id,
            "page": (
                _as_int(metadata["page"])
                if metadata.get("page") is not None
                else None
            ),
            "score": None,
        }

    async def retrieve(
        self,
        query: str,
        owner_user_id: int,
        kb_id: int,
        top_k: int,
        similarity_threshold: float | None = None,
    ) -> list[RetrievedChunk]:
        # Owner isolation: only the KB owner may query its collection
        factory = self._session_factory
        async with factory() as session:
            kb = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == kb_id,
                    KnowledgeBase.owner_user_id == owner_user_id,
                )
            )
        if kb is None:
            logger.warning("Retrieval denied: KB %s not owned by user %s", kb_id, owner_user_id)
            return []

        client = self._get_client()
        if client is None:
            return []

        collection_name = kb.active_collection or f"kb_{kb_id}_v1"
        try:
            collection = client.get_collection(name=collection_name)
        except Exception as exc:
            logger.warning("Chroma collection %r unavailable: %s", collection_name, exc)
            return []
        try:
            candidate_k = max(top_k * 4, 20)
            if self._relay is not None:
                query_embeddings = await self._relay.embed([query])
                result = collection.query(
                    query_embeddings=query_embeddings, n_results=candidate_k
                )
            else:
                # 无 relay（测试/降级）：让 Chroma 用默认 embedding function
                result = collection.query(query_texts=[query], n_results=candidate_k)
        except Exception as exc:
            logger.warning("Chroma query on %r failed: %s", collection_name, exc)
            return []
        chunks = self._to_chunks(result)
        # frozen dataclass 不能原地赋值，逐项重建补上来源知识库快照
        chunks = [replace(c, kb_id=kb_id, kb_name=kb.name) for c in chunks]
        if similarity_threshold is not None:
            chunks = [c for c in chunks if c.score >= similarity_threshold]
        chunks = [
            replace(
                chunk,
                rank_score=hybrid_score(
                    chunk.score,
                    lexical_score(query, chunk.content),
                ),
            )
            for chunk in chunks
        ]
        chunks.sort(key=lambda chunk: chunk.ordering_score, reverse=True)
        return chunks[:top_k]

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
        if not chunks or seed_count <= 0 or self._relay is None:
            return pack_context(chunks, max_chunks=max_chunks, max_tokens=max_tokens)

        try:
            query_embeddings = await self._query_embeddings([query])
            if not query_embeddings or not query_embeddings[0]:
                raise ValueError("query embedding is unavailable")
            query_embedding = [float(value) for value in query_embeddings[0]]
            client = self._get_client()
            if client is None:
                raise RuntimeError("Chroma client is unavailable")

            core_by_key = {
                (chunk.kb_id, chunk.chunk_id): chunk
                for chunk in chunks
            }
            seed_groups: dict[
                tuple[int, int], list[tuple[RetrievedChunk, int]]
            ] = {}
            for seed in chunks[:seed_count]:
                if seed.kb_id is None:
                    raise ValueError(f"core chunk {seed.chunk_id!r} has no kb_id")
                seed_index = seed.chunk_index
                if seed_index is None:
                    seed_index = _chunk_index_from_id(seed.chunk_id)
                if seed_index is None:
                    raise ValueError(f"core chunk {seed.chunk_id!r} has no chunk index")
                seed_groups.setdefault((seed.kb_id, seed.doc_id), []).append(
                    (seed, seed_index)
                )

            expanded_windows: list[RetrievedChunk] = []
            for (kb_id, doc_id), grouped_seeds in seed_groups.items():
                factory = self._session_factory
                async with factory() as session:
                    kb = await session.scalar(
                        select(KnowledgeBase).where(
                            KnowledgeBase.id == kb_id,
                            KnowledgeBase.owner_user_id == owner_user_id,
                        )
                    )
                if kb is None:
                    raise PermissionError(
                        f"KB {kb_id} is unavailable to user {owner_user_id}"
                    )

                collection_name = kb.active_collection or f"kb_{kb_id}_v1"
                collection = client.get_collection(name=collection_name)
                wanted_indices = sorted(
                    {
                        index + delta
                        for _, index in grouped_seeds
                        for delta in (-1, 0, 1)
                    }
                )
                result = collection.get(
                    where={
                        "$and": [
                            {"doc_id": {"$eq": doc_id}},
                            {"chunk_index": {"$in": wanted_indices}},
                        ]
                    },
                    include=["documents", "metadatas", "embeddings"],
                )
                if not _flat_result_values(result, "ids"):
                    result = collection.get(
                        where={"doc_id": doc_id},
                        include=["documents", "metadatas", "embeddings"],
                    )
                for seed, seed_index in grouped_seeds:
                    window = _neighbor_window(
                        result,
                        seed=seed,
                        seed_index=seed_index,
                        query_embedding=query_embedding,
                        kb_name=kb.name,
                        core_by_key=core_by_key,
                    )
                    if not window:
                        raise ValueError(
                            f"no adjacent window found for chunk {seed.chunk_id!r}"
                        )
                    expanded_windows.extend(window)

            neighbor_candidates = [
                chunk
                for chunk in expanded_windows
                if (chunk.kb_id, chunk.chunk_id) not in core_by_key
            ]
            selected = pack_context(
                [*chunks, *neighbor_candidates],
                max_chunks=max_chunks,
                max_tokens=max_tokens,
            )
            selected_ids = {chunk.chunk_id for chunk in selected}
            ordered: list[RetrievedChunk] = []
            emitted: set[str] = set()
            for chunk in [*expanded_windows, *chunks]:
                if chunk.chunk_id in selected_ids and chunk.chunk_id not in emitted:
                    ordered.append(chunk)
                    emitted.add(chunk.chunk_id)
            return ordered
        except Exception as exc:
            logger.warning("Adjacent context expansion failed; using core chunks: %s", exc)
            return pack_context(chunks, max_chunks=max_chunks, max_tokens=max_tokens)

    @staticmethod
    def _to_chunks(result: dict[str, Any]) -> list[RetrievedChunk]:
        ids = (result.get("ids") or [[]])[0] or []
        documents = (result.get("documents") or [[]])[0] or []
        metadatas = (result.get("metadatas") or [[]])[0] or []
        distances = (result.get("distances") or [[]])[0] or []

        chunks: list[RetrievedChunk] = []
        for i in range(len(ids)):
            meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            distance = distances[i] if i < len(distances) else None
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(ids[i]),
                    content=documents[i] if i < len(documents) and documents[i] is not None else "",
                    doc_id=_as_int(meta.get("doc_id")),
                    title=str(meta.get("title") or ""),
                    page=_as_int(meta["page"]) if meta.get("page") is not None else None,
                    # 集合创建时固定 hnsw:space=cosine，余弦距离取值 [0,2]：
                    # score=1-distance 即余弦相似度（负值=无关）
                    score=1.0 - float(distance) if distance is not None else 0.0,
                    chunk_index=_resolve_chunk_index(meta, str(ids[i])),
                )
            )
        return chunks


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunk_index_from_id(chunk_id: str) -> int | None:
    parts = chunk_id.split(":", 2)
    return _optional_int(parts[1]) if len(parts) >= 2 else None


def _chunk_offset_from_id(chunk_id: str) -> int:
    if "#" not in chunk_id:
        return 0
    offset = _optional_int(chunk_id.rsplit("#", 1)[1])
    return offset if offset is not None else 0


def _resolve_chunk_index(metadata: dict[str, Any], chunk_id: str) -> int | None:
    index = _optional_int(metadata.get("chunk_index"))
    return index if index is not None else _chunk_index_from_id(chunk_id)


def _flat_result_values(result: dict[str, Any], key: str) -> list[Any]:
    values = result.get(key)
    if values is None:
        return []
    if hasattr(values, "tolist"):
        values = values.tolist()
    if isinstance(values, tuple):
        values = list(values)
    if not isinstance(values, list):
        return []
    if len(values) == 1 and isinstance(values[0], (list, tuple)):
        first = list(values[0])
        if key != "embeddings" or not first or isinstance(first[0], (list, tuple)):
            return first
    return values


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _neighbor_window(
    result: dict[str, Any],
    *,
    seed: RetrievedChunk,
    seed_index: int,
    query_embedding: list[float],
    kb_name: str,
    core_by_key: dict[tuple[int | None, str], RetrievedChunk],
) -> list[RetrievedChunk]:
    ids = _flat_result_values(result, "ids")
    documents = _flat_result_values(result, "documents")
    metadatas = _flat_result_values(result, "metadatas")
    embeddings = _flat_result_values(result, "embeddings")
    wanted = {seed_index - 1, seed_index, seed_index + 1}
    indexed: list[tuple[tuple[int, int], RetrievedChunk]] = []
    for position, raw_id in enumerate(ids):
        chunk_id = str(raw_id)
        metadata = (
            metadatas[position]
            if position < len(metadatas) and isinstance(metadatas[position], dict)
            else {}
        )
        index = _resolve_chunk_index(metadata, chunk_id)
        doc_id = _optional_int(metadata.get("doc_id"))
        if index not in wanted or doc_id != seed.doc_id:
            continue

        core = core_by_key.get((seed.kb_id, chunk_id))
        ordering_key = (index, _chunk_offset_from_id(chunk_id))
        if core is not None:
            indexed.append((ordering_key, core))
            continue
        if position >= len(embeddings):
            raise ValueError(f"stored embedding missing for chunk {chunk_id!r}")
        raw_embedding = embeddings[position]
        if hasattr(raw_embedding, "tolist"):
            raw_embedding = raw_embedding.tolist()
        if not isinstance(raw_embedding, (list, tuple)):
            raise ValueError(f"stored embedding malformed for chunk {chunk_id!r}")
        embedding = [float(value) for value in raw_embedding]
        content = (
            str(documents[position])
            if position < len(documents) and documents[position] is not None
            else ""
        )
        page = _optional_int(metadata.get("page"))
        indexed.append(
            (
                ordering_key,
                RetrievedChunk(
                    chunk_id=chunk_id,
                    content=content,
                    doc_id=seed.doc_id,
                    title=str(metadata.get("title") or seed.title),
                    page=page,
                    score=_cosine_similarity(query_embedding, embedding),
                    kb_id=seed.kb_id,
                    kb_name=kb_name,
                    chunk_index=index,
                    is_neighbor=True,
                ),
            )
        )
    indexed.sort(key=lambda item: item[0])
    return [chunk for _, chunk in indexed]
