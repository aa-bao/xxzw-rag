from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from sqlalchemy import select

from src.db.models import KnowledgeBase
from src.retrieval.module import RetrievedChunk, RetrievalModule

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
            if self._relay is not None:
                query_embeddings = await self._relay.embed([query])
                result = collection.query(
                    query_embeddings=query_embeddings, n_results=top_k
                )
            else:
                # 无 relay（测试/降级）：让 Chroma 用默认 embedding function
                result = collection.query(query_texts=[query], n_results=top_k)
        except Exception as exc:
            logger.warning("Chroma query on %r failed: %s", collection_name, exc)
            return []
        chunks = self._to_chunks(result)
        # frozen dataclass 不能原地赋值，逐项重建补上来源知识库快照
        chunks = [replace(c, kb_id=kb_id, kb_name=kb.name) for c in chunks]
        if similarity_threshold is not None:
            chunks = [c for c in chunks if c.score >= similarity_threshold]
        return chunks

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
