"""文档入库 worker：消费 pending 的 ingest job，完成 解析→分块→向量化→写入索引。

在 FastAPI 启动时作为后台 asyncio 任务运行，轮询 DocumentJob 表中
job_type='ingest' 且 status='pending' 的作业。每个 job 走四阶段，每阶段
更新 job.stage 与 doc.status，前端轮询即可看到实时进展。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from src.db.models import (
    Document,
    DocumentJob,
    DocumentMapping,
    KnowledgeBase,
    MappingTemplateVersion,
)
from src.ingestion.factory import ParserFactory
from src.ingestion.splitter import split_text
from src.models.client import ModelError, ModelRelayClient
from src.retrieval.chroma import ChromaRetrieval
from src.structured.models import MappingDefinition, RecordTypeMapping
from src.structured.paths import compile_path
from src.structured.stream import iter_source
from src.structured.transforms import MappingContext, map_record

logger = logging.getLogger(__name__)

# 单次 embedding 请求的最大文本条数（防上游超时）
_EMBED_BATCH_SIZE = 64
# 错误信息入库上限
_ERROR_TRUNCATE = 500
# job 最大重试次数
_MAX_ATTEMPTS = 3
_MAX_EMBED_CHARS = 500


class IngestWorker:
    def __init__(
        self,
        session_factory: object,
        *,
        upload_root: Path,
        relay: ModelRelayClient,
        chroma: ChromaRetrieval,
        parser_factory: ParserFactory | None = None,
        poll_interval: float = 1.0,
        max_attempts: int = _MAX_ATTEMPTS,
        embed_batch_size: int = _EMBED_BATCH_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._upload_root = upload_root
        self._relay = relay
        self._chroma = chroma
        self._parser_factory = parser_factory or ParserFactory()
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._embed_batch_size = embed_batch_size
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="ingest-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        logger.info("Ingest worker started (poll interval %ss)", self._poll_interval)
        while not self._stop.is_set():
            try:
                job = await self._claim_next_job()
            except Exception:
                logger.exception("Ingest worker failed to claim job")
                job = None
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._process_job(job)

    # ── job 领取：查 id → 条件 UPDATE 抢占（WHERE status='pending' 保证原子性） ──
    async def _claim_next_job(self) -> DocumentJob | None:
        factory = self._session_factory
        async with factory() as session:
            candidate_id = await session.scalar(
                select(DocumentJob.id)
                .where(
                    DocumentJob.job_type == "ingest",
                    DocumentJob.status == "pending",
                    DocumentJob.attempts < self._max_attempts,
                )
                .order_by(DocumentJob.id)
                .limit(1)
            )
            if candidate_id is None:
                return None

            result = await session.execute(
                update(DocumentJob)
                .where(
                    DocumentJob.id == candidate_id,
                    DocumentJob.status == "pending",
                )
                .values(status="running", started_at=datetime.now(UTC))
            )
            await session.commit()
            if result.rowcount == 0:
                # 被其他实例抢先，本循环跳过
                return None

            job = await session.scalar(
                select(DocumentJob).where(DocumentJob.id == candidate_id)
            )
            return job

    async def _process_job(self, job: DocumentJob) -> None:
        factory = self._session_factory
        try:
            async with factory() as session:
                doc = await session.scalar(
                    select(Document).where(Document.id == job.doc_id)
                )
                kb = (
                    await session.scalar(
                        select(KnowledgeBase).where(KnowledgeBase.id == doc.kb_id)
                    )
                    if doc is not None
                    else None
                )
                if doc is None or kb is None:
                    await self._fail_job(job.id, "文档或知识库不存在")
                    return

                # ① parsing：读文件 + 解析文本；回填 content_hash（供上传查重用）
                structured = Path(doc.title).suffix.lower() in {".json", ".jsonl"}
                mapping_definition: MappingDefinition | None = None
                mapping_version_id = 0
                if structured:
                    mapping_row = (
                        await session.execute(
                            select(
                                MappingTemplateVersion.id,
                                MappingTemplateVersion.mapping_json,
                            )
                            .join(
                                DocumentMapping,
                                DocumentMapping.mapping_version_id
                                == MappingTemplateVersion.id,
                            )
                            .where(DocumentMapping.document_id == doc.id)
                        )
                    ).one_or_none()
                    if mapping_row is None:
                        await self._fail_job(job.id, "JSON 文档尚未绑定映射模板")
                        return
                    mapping_version_id = int(mapping_row.id)
                    mapping_definition = MappingDefinition.model_validate(
                        json.loads(mapping_row.mapping_json)
                    )

                if not await self._set_stage(job.id, doc.id, "parsing"):
                    return  # 文档处理中被删除
                text = ""
                if not structured:
                    sections = await asyncio.to_thread(self._parse_file, doc, kb)
                    text = "\n".join(s.text for s in sections if s.text)
                if doc.content_hash is None:
                    doc.content_hash = hashlib.sha256(
                        (self._upload_root / doc.file_path).read_bytes()
                    ).hexdigest()

                # ② chunking：分块
                if not await self._set_stage(job.id, doc.id, "chunking"):
                    return
                if structured and mapping_definition is not None:
                    chunks = await asyncio.to_thread(
                        self._structured_chunks,
                        doc,
                        mapping_definition,
                        mapping_version_id,
                        kb.chunk_size,
                        kb.overlap,
                    )
                else:
                    chunks = split_text(
                        doc.id,
                        text,
                        chunk_size=kb.chunk_size,
                        overlap=kb.overlap,
                    )
                if not chunks:
                    await self._fail_job(job.id, "文档内容为空，未生成分块")
                    return

                # ③ embedding：分批向量化
                # 上游 embedding 服务限制单条 ≤500 字（约 512 token，实测 520 字 400 错误）。
                # 超长 chunk（如大表格行）先按字符硬切为 ≤500 字的子片段，保内容不丢。
                if not await self._set_stage(job.id, doc.id, "embedding"):
                    return
                embed_items: list[dict[str, Any]] = []
                for c in chunks:
                    content = c["content"]
                    embedding_text = c.get("embedding_text") or content
                    preserve_full_content = bool(c.get("preserve_full_content"))
                    if len(embedding_text) <= _MAX_EMBED_CHARS or preserve_full_content:
                        embed_items.append(
                            {
                                "chunk_id": c["chunk_id"],
                                "content": content,
                                "embedding_text": embedding_text[:_MAX_EMBED_CHARS],
                                "chunk_index": c["index"],
                                **self._structured_metadata(c),
                            }
                        )
                        continue
                    for idx in range(0, len(content), _MAX_EMBED_CHARS):
                        piece = content[idx : idx + _MAX_EMBED_CHARS]
                        if piece.strip():
                            embed_items.append(
                                {
                                    "chunk_id": f"{c['chunk_id']}#{idx}",
                                    "content": piece,
                                    "embedding_text": piece,
                                    "chunk_index": c["index"],
                                    **self._structured_metadata(c),
                                }
                            )

                embeddings: list[list[float]] = []
                for i in range(0, len(embed_items), self._embed_batch_size):
                    batch = embed_items[i : i + self._embed_batch_size]
                    vectors = await self._relay.embed([it["embedding_text"] for it in batch])
                    embeddings.extend(vectors)

                # ④ indexing：写入向量库（用拆分后的子片段，每个独立向量）
                if not await self._set_stage(job.id, doc.id, "indexing"):
                    return
                collection_name = kb.active_collection or f"kb_{kb.id}_v1"
                payload = [
                    {
                        "chunk_id": it["chunk_id"],
                        "content": it["content"],
                        "doc_id": doc.id,
                        "title": doc.title,
                        "page": None,
                        "chunk_index": it["chunk_index"],
                        **self._structured_metadata(it),
                    }
                    for it in embed_items
                ]
                await self._chroma.upsert_chunks(collection_name, payload, embeddings)

                # 完成：更新 doc + job（execute update，job 可能 detached）
                now = datetime.now(UTC)
                # 竞态防护：入库完成前用户可能已删除该文档（软删 deleting）——
                # 被删文档不再写回 done/hash，避免删除被覆盖导致查重残留
                current_doc_status = await session.scalar(
                    select(Document.status).where(Document.id == doc.id)
                )
                if current_doc_status == "deleting":
                    await session.execute(
                        update(DocumentJob)
                        .where(DocumentJob.id == job.id)
                        .values(status="done", stage=None, finished_at=now)
                    )
                    await session.commit()
                    logger.info(
                        "Doc %s was deleted during ingest — skip status update", doc.id
                    )
                    return

                await session.execute(
                    update(Document)
                    .where(Document.id == doc.id)
                    .values(status="done", chunk_count=len(embed_items), ingested_at=now)
                )
                await session.execute(
                    update(DocumentJob)
                    .where(DocumentJob.id == job.id)
                    .values(status="done", stage=None, finished_at=now)
                )
                await session.commit()
                logger.info(
                    "Doc %s ingested: %d chunks → %s",
                    doc.id, len(chunks), collection_name,
                )
        except Exception as exc:
            message = _readable_error(exc)
            logger.exception("Ingest failed for job %s", job.id)
            await self._fail_job(job.id, message)

    def _parse_file(self, doc: Document, kb: KnowledgeBase) -> list[Any]:
        path = self._upload_root / doc.file_path
        data = path.read_bytes()
        # ParserFactory 需要文件名（取后缀）与 media_type；txt 统一按 text/plain
        return self._parser_factory.parse(doc.title, "text/plain", data)

    def _structured_chunks(
        self,
        doc: Document,
        definition: MappingDefinition,
        mapping_version_id: int,
        chunk_size: int,
        overlap: int,
    ) -> list[dict[str, Any]]:
        source_path = self._upload_root / doc.file_path
        source_hash = doc.content_hash or hashlib.sha256(source_path.read_bytes()).hexdigest()
        context = MappingContext(
            source_hash=source_hash,
            mapping_version_id=mapping_version_id,
        )
        policies: dict[str, str] = {}

        def collect(mapping: RecordTypeMapping) -> None:
            policies[mapping.name] = mapping.chunk_policy
            for child in mapping.children:
                collect(child)

        for root in definition.record_types:
            collect(root)

        chunks: list[dict[str, Any]] = []
        for root in definition.record_types:
            record_path = root.record_path
            if definition.source_format == "json" and record_path == "$":
                with source_path.open("rb") as handle:
                    first = next(
                        (chr(value) for value in handle.read(64) if not chr(value).isspace()),
                        "",
                    )
                if first == "[":
                    record_path = "$[*]"
            compiled = compile_path(record_path)
            for source in iter_source(source_path, definition.source_format, compiled):
                for record in map_record(source, root, context):
                    policy = policies.get(record.record_type, "semantic")
                    if policy in {"ignore", "parent-only"}:
                        continue
                    text = "\n\n".join(
                        value for value in (record.title, record.content) if value
                    ).strip()
                    if not text:
                        continue
                    if policy == "semantic":
                        pieces = split_text(
                            doc.id,
                            text,
                            chunk_size=chunk_size,
                            overlap=overlap,
                        )
                    elif policy == "topic":
                        pieces = self._topic_retrieval_entries(text)
                    else:
                        pieces = [{
                            "content": text,
                            "embedding_text": text[:_MAX_EMBED_CHARS],
                            "preserve_full_content": True,
                        }]
                    for ordinal, piece in enumerate(pieces):
                        content = str(piece["content"])
                        embedding_text = str(piece.get("embedding_text") or content)
                        content_hash = hashlib.sha256(
                            f"{content}\0{embedding_text}".encode("utf-8")
                        ).hexdigest()
                        chunks.append(
                            {
                                "chunk_id": (
                                    f"{doc.id}:{record.record_id}:{ordinal}:"
                                    f"{content_hash[:12]}"
                                ),
                                "content": content,
                                "embedding_text": embedding_text,
                                "preserve_full_content": bool(
                                    piece.get("preserve_full_content")
                                ),
                                "index": len(chunks),
                                "record_id": record.record_id,
                                "parent_id": record.parent_id,
                                "record_type": record.record_type,
                                "source_pointer": record.source_pointer,
                                "mapping_version_id": mapping_version_id,
                            }
                        )
        return chunks

    @staticmethod
    def _topic_retrieval_entries(text: str) -> list[dict[str, Any]]:
        """Create small retrieval entries that all return the complete topic.

        Paragraphs are retrieval representations only.  The stored document stays
        the complete mapped record, so a hit never loses its question/answer context.
        """
        paragraphs = [
            part.strip(" ，。！？；:：")
            for part in re.split(r"[\n。！？；]+", text)
            if part.strip(" ，。！？；:：")
        ]
        if not paragraphs:
            return []
        entries: list[str] = []
        for paragraph in paragraphs:
            for start in range(0, len(paragraph), _MAX_EMBED_CHARS):
                entry = paragraph[start : start + _MAX_EMBED_CHARS].strip()
                if entry and entry not in entries:
                    entries.append(entry)
        return [
            {
                "content": text,
                "embedding_text": entry,
                "preserve_full_content": True,
            }
            for entry in entries
        ]

    @staticmethod
    def _structured_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "record_id",
            "parent_id",
            "record_type",
            "source_pointer",
            "mapping_version_id",
        )
        return {key: chunk[key] for key in keys if key in chunk}

    async def _set_stage(self, job_id: int, doc_id: int, stage: str) -> bool:
        """更新 job 阶段与 doc 状态；文档已被删除（deleting）时不动 doc，标记 job 完成并返回 False。"""
        factory = self._session_factory
        async with factory() as session:
            await session.execute(
                update(DocumentJob)
                .where(DocumentJob.id == job_id)
                .values(stage=stage)
            )
            # 条件更新：仅当 doc 未被删除时才置 running，避免覆盖软删标记
            result = await session.execute(
                update(Document)
                .where(Document.id == doc_id, Document.status != "deleting")
                .values(status="running")
            )
            if result.rowcount == 0:
                # 文档已被删除：job 标记完成（不再重试），doc 保持 deleting
                await session.execute(
                    update(DocumentJob)
                    .where(DocumentJob.id == job_id)
                    .values(status="done", stage=None, finished_at=datetime.now(UTC))
                )
                await session.commit()
                logger.info("Doc %s deleted during ingest — job %s finished", doc_id, job_id)
                return False
            await session.commit()
            return True

    async def _fail_job(self, job_id: int, message: str) -> None:
        factory = self._session_factory
        async with factory() as session:
            job = await session.scalar(select(DocumentJob).where(DocumentJob.id == job_id))
            if job is None:
                return
            job.status = "failed"
            job.stage = None
            job.finished_at = datetime.now(UTC)
            job.error_message = message[:_ERROR_TRUNCATE]
            await session.execute(
                update(Document)
                .where(Document.id == job.doc_id)
                .values(status="failed", error_message=message[:_ERROR_TRUNCATE])
            )
            await session.commit()


def _readable_error(exc: Exception) -> str:
    if isinstance(exc, ModelError):
        return f"{exc.code}: {exc.message}"
    if isinstance(exc, (OSError, UnicodeDecodeError)):
        return str(exc)
    # AppError 与未知异常：取 message 或类名
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message
    return exc.__class__.__name__
