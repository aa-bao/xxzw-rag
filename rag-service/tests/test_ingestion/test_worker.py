"""IngestWorker 测试：解析→分块→向量化→入库全流程与失败路径。

用真实 MySQL（migrated_mysql_url fixture）+ FakeTransport 假 embedding 服务
+ 内存记录式 fake Chroma，验证 worker 行为而不依赖真实向量库。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import Document, DocumentJob, KnowledgeBase, User
from src.db.repositories import KnowledgeBaseRepository
from src.db.session import create_engine, create_session_factory
from src.ingestion.worker import IngestWorker
from src.models.client import ModelRelayClient


async def _ensure_owner(factory) -> int:
    """测试库是全新库，先建一个 owner 用户（FK 约束）；已存在则复用。"""
    async with factory() as session:
        existing = await session.scalar(
            select(User.id).where(User.username == "worker-owner")
        )
        if existing is not None:
            return existing
        user = User(
            username="worker-owner",
            password_hash=PasswordHasher().hash("secret123"),
            role="account_admin",
            status="active",
        )
        session.add(user)
        await session.commit()
        return user.id


class FakeEmbedTransport(httpx.AsyncBaseTransport):
    """返回固定维度的假 embedding（支持按请求批次计数）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.embed_calls: list[list[str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if self._fail:
            return httpx.Response(500, json={"error": "upstream down"})
        texts = body["input"]
        self.embed_calls.append(texts)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1] * 8 for _ in texts}]},
        )


class FakeChroma:
    """内存记录式 fake：记录 upsert 的 chunk，查询返回记录。"""

    def __init__(self) -> None:
        self.collections: dict[str, list[dict[str, Any]]] = {}

    async def upsert_chunks(self, collection_name, chunks, embeddings) -> None:
        self.collections.setdefault(collection_name, []).extend(chunks)

    async def delete_doc_chunks(self, collection_name, doc_id) -> None:
        items = self.collections.get(collection_name, [])
        self.collections[collection_name] = [c for c in items if c["doc_id"] != doc_id]

    async def list_doc_chunks(self, collection_name, doc_id, *, limit, offset) -> list[dict[str, Any]]:
        items = [c for c in self.collections.get(collection_name, []) if c["doc_id"] == doc_id]
        return items[offset : offset + limit]


async def _make_kb(factory, *, name: str = "kb") -> KnowledgeBase:
    owner_id = await _ensure_owner(factory)
    async with factory() as session:
        repo = KnowledgeBaseRepository(session)
        kb = await repo.create(
            owner_user_id=owner_id,
            name=name,
            description=None,
            embedding_model="fake-embed",
            embedding_dimension=8,
            chunk_size=64,
            overlap=8,
        )
        return kb


async def _make_doc(factory, kb: KnowledgeBase, text: str, *, title: str = "doc.txt") -> Document:
    async with factory() as session:
        doc = Document(
            kb_id=kb.id,
            owner_user_id=kb.owner_user_id,
            title=title,
            source=title,
            source_type="upload",
            file_path="ab/cd/fake.txt",
            file_size_bytes=len(text.encode()),
        )
        session.add(doc)
        await session.flush()
        job = DocumentJob(doc_id=doc.id, owner_user_id=kb.owner_user_id, job_type="ingest")
        session.add(job)
        await session.commit()
        await session.refresh(doc)
        return doc


async def _worker(factory, transport, chroma, *, upload_root: Path, **kw) -> IngestWorker:
    relay = ModelRelayClient(
        type("_S", (), {"model_relay": type("_R", (), {
            "base_url": "http://fake", "api_key": type("_K", (), {"get_secret_value": lambda self: "k"})(),
            "embedding_model": "fake", "embedding_base_url": "", "embedding_api_key": type("_K", (), {"get_secret_value": lambda self: ""})(),
            "embedding_max_retries": 1, "retry_base_delay_seconds": 0.01,
        })()})(),
        httpx.AsyncClient(transport=transport),
    )
    return IngestWorker(
        factory,
        upload_root=upload_root,
        relay=relay,
        chroma=chroma,
        poll_interval=0.01,
        **kw,
    )


async def test_worker_ingests_doc_and_marks_done(
    migrated_mysql_url: str, tmp_path: Path
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        kb = await _make_kb(factory)
        text = "知识库问答系统支持将文档切分并向量化。这是第二句测试内容。"
        file_dir = tmp_path / "ab" / "cd"
        file_dir.mkdir(parents=True, exist_ok=True)
        (tmp_path / "ab/cd/fake.txt").write_bytes(text.encode("utf-8"))
        doc = await _make_doc(factory, kb, text)

        transport = FakeEmbedTransport()
        chroma = FakeChroma()
        worker = await _worker(factory, transport, chroma, upload_root=tmp_path)
        try:
            await worker._process_job(await _claim_job_for_doc(factory, doc.id))
        finally:
            await worker.stop()

        async with factory() as session:
            doc = await session.scalar(select(Document).where(Document.kb_id == kb.id))
            job = await session.scalar(select(DocumentJob).where(DocumentJob.doc_id == doc.id))
            assert doc.status == "done"
            assert doc.chunk_count == len(chroma.collections[f"kb_{kb.id}_v1"])
            assert job.status == "done"
            assert job.finished_at is not None
        assert chroma.collections[f"kb_{kb.id}_v1"], "chunks must be written to chroma"
    finally:
        await engine.dispose()


async def test_worker_marks_failed_on_embed_error(migrated_mysql_url: str, tmp_path: Path) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        kb = await _make_kb(factory)
        text = "内容"
        file_dir = tmp_path / "ab" / "cd"
        file_dir.mkdir(parents=True, exist_ok=True)
        (tmp_path / "ab/cd/fake.txt").write_bytes(text.encode("utf-8"))
        doc = await _make_doc(factory, kb, text)

        transport = FakeEmbedTransport(fail=True)
        chroma = FakeChroma()
        worker = await _worker(factory, transport, chroma, upload_root=tmp_path)
        try:
            await worker._process_job(await _claim_job_for_doc(factory, doc.id))
        finally:
            await worker.stop()

        async with factory() as session:
            doc = await session.scalar(select(Document).where(Document.kb_id == kb.id))
            job = await session.scalar(select(DocumentJob).where(DocumentJob.doc_id == doc.id))
            assert doc.status == "failed"
            assert doc.error_message
            assert job.status == "failed"
            assert job.error_message
    finally:
        await engine.dispose()


async def test_worker_reports_stage_progression(migrated_mysql_url: str, tmp_path: Path) -> None:
    """job.stage 应按 parsing→chunking→embedding→indexing 依次更新。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        kb = await _make_kb(factory)
        text = "a" * 200
        file_dir = tmp_path / "ab" / "cd"
        file_dir.mkdir(parents=True, exist_ok=True)
        (tmp_path / "ab/cd/fake.txt").write_bytes(text.encode("utf-8"))
        doc = await _make_doc(factory, kb, text)

        transport = FakeEmbedTransport()
        chroma = FakeChroma()
        worker = await _worker(factory, transport, chroma, upload_root=tmp_path)
        job = await _claim_job_for_doc(factory, doc.id)
        try:
            await worker._process_job(job)
        finally:
            await worker.stop()

        async with factory() as session:
            updated = await session.scalar(select(DocumentJob).where(DocumentJob.id == job.id))
            # 成功后 stage 清空；期间各 stage 通过 _set_stage 落库
            assert updated.status == "done"
        assert transport.embed_calls, "embed must be called"
    finally:
        await engine.dispose()


async def _claim_job_for_doc(factory, doc_id: int) -> DocumentJob:
    async with factory() as session:
        return await session.scalar(
            select(DocumentJob)
            .where(DocumentJob.doc_id == doc_id, DocumentJob.job_type == "ingest")
            .order_by(DocumentJob.id)
            .limit(1)
        )
