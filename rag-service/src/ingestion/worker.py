"""文档入库 worker：消费 pending 的 ingest job，完成 解析→分块→向量化→写入索引。

在 FastAPI 启动时作为后台 asyncio 任务运行，轮询 DocumentJob 表中
job_type='ingest' 且 status='pending' 的作业。每个 job 走四阶段，每阶段
更新 job.stage 与 doc.status，前端轮询即可看到实时进展。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from src.db.models import Document, DocumentJob, KnowledgeBase
from src.ingestion.factory import ParserFactory
from src.ingestion.splitter import split_text
from src.models.client import ModelError, ModelRelayClient
from src.retrieval.chroma import ChromaRetrieval

logger = logging.getLogger(__name__)

# 单次 embedding 请求的最大文本条数（防上游超时）
_EMBED_BATCH_SIZE = 64
# 错误信息入库上限
_ERROR_TRUNCATE = 500
# job 最大重试次数
_MAX_ATTEMPTS = 3


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
                if not await self._set_stage(job.id, doc.id, "parsing"):
                    return  # 文档处理中被删除
                sections = await asyncio.to_thread(
                    self._parse_file, doc, kb
                )
                text = "\n".join(s.text for s in sections if s.text)
                if doc.content_hash is None:
                    doc.content_hash = hashlib.sha256(
                        (self._upload_root / doc.file_path).read_bytes()
                    ).hexdigest()

                # ② chunking：分块
                if not await self._set_stage(job.id, doc.id, "chunking"):
                    return
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
                MAX_EMBED_CHARS = 500
                embed_items: list[dict[str, str]] = []
                for c in chunks:
                    content = c["content"]
                    if len(content) <= MAX_EMBED_CHARS:
                        embed_items.append({"chunk_id": c["chunk_id"], "content": content})
                        continue
                    for idx in range(0, len(content), MAX_EMBED_CHARS):
                        piece = content[idx : idx + MAX_EMBED_CHARS]
                        if piece.strip():
                            embed_items.append(
                                {"chunk_id": f"{c['chunk_id']}#{idx}", "content": piece}
                            )

                embeddings: list[list[float]] = []
                for i in range(0, len(embed_items), self._embed_batch_size):
                    batch = embed_items[i : i + self._embed_batch_size]
                    vectors = await self._relay.embed([it["content"] for it in batch])
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
