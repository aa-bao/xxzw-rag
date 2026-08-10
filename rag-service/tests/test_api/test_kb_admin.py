from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import (
    Conversation,
    ConversationKb,
    Document,
    DocumentJob,
    KnowledgeBase,
    Message,
    QueryLog,
    Reference,
    User,
)
from src.db.session import create_engine, create_session_factory
from src.shared.config import Settings


def _settings(database_url: str, upload_root: str = "./data/uploads") -> Settings:
    return Settings.model_validate(
        {
            "app": {"browser_origin": "http://127.0.0.1:8000", "secure_cookie": False},
            "rag": {
                "chroma_mode": "persist",
                "chroma_persist_dir": "./data/chroma",
                "default_chunk_size": 512,
                "default_overlap": 50,
                "top_k": 5,
                "similarity_threshold": 0.2,
            },
            "model_relay": {
                "base_url": "http://127.0.0.1:9000/v1",
                "api_key": "test-key",
                "embedding_model": "test-embedding",
                "chat_model": "test-chat",
                "timeout_seconds": 60,
                "embedding_max_retries": 3,
                "chat_pre_stream_max_retries": 1,
                "retry_base_delay_seconds": 0.01,
            },
            "database": {"url": database_url, "pool_size": 2, "pool_recycle_seconds": 1800},
            "upload": {
                "root_dir": upload_root,
                "temp_dir": f"{upload_root}_tmp",
                "max_size_mb": 100,
                "allowed_extensions": ["txt"],
            },
            "llm": {
                "temperature": 0.7,
                "max_tokens": 4096,
                "max_history_tokens": 4096,
                "system_prompt": "system",
            },
            "empty_response": "empty",
        }
    )


@pytest.fixture
def upload_root(tmp_path: Path) -> str:
    root = tmp_path / "uploads"
    root.mkdir()
    return str(root)


async def _seed_admin(factory, *, role: str = "account_admin", prefix: str = "admin") -> tuple[str, int]:
    """创建用户并返回 (username, kb_id)；默认 admin 角色并创建其知识库。"""
    hasher = PasswordHasher()
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    async with factory() as session:
        user = User(
            username=username,
            password_hash=hasher.hash("secret"),
            role=role,
            status="active",
        )
        session.add(user)
        await session.flush()
        kb_id = None
        if role == "account_admin":
            kb = KnowledgeBase(
                owner_user_id=user.id,
                name="seed-kb",
                embedding_model="test",
                embedding_dimension=128,
                active_collection="kb_seed_v1",
            )
            session.add(kb)
            await session.flush()
            kb_id = kb.id
        await session.commit()
        return username, kb_id


async def _login(client: AsyncClient, username: str, password: str = "secret") -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.cookies["rag_session"]


async def test_employee_cannot_get_or_update_or_delete_kb(
    migrated_mysql_url: str,
) -> None:
    """员工访问管理员接口（详情/更新/删除）一律 403。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)
        emp_username, _ = await _seed_admin(factory, role="user", prefix="emp")

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, emp_username)
            for method, url, kwargs in [
                ("get", f"/api/kb/{kb_id}", {}),
                ("put", f"/api/kb/{kb_id}", {"json": {"name": "x"}}),
                ("delete", f"/api/kb/{kb_id}", {}),
                ("post", f"/api/kb/{kb_id}/test", {"json": {"question": "x"}}),
            ]:
                resp = await getattr(client, method)(url, cookies={"rag_session": cookie}, **kwargs)
                assert resp.status_code == 403, f"{method} {url}: {resp.text}"
                assert resp.json()["error"]["code"] == "FORBIDDEN"
    finally:
        await engine.dispose()


async def test_admin_get_update_delete_kb_flow(
    migrated_mysql_url: str,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username)

            # GET 详情
            resp = await client.get(f"/api/kb/{kb_id}", cookies={"rag_session": cookie})
            assert resp.status_code == 200
            assert resp.json()["data"]["name"] == "seed-kb"

            # PUT 更新
            resp = await client.put(
                f"/api/kb/{kb_id}",
                json={"name": "renamed", "top_k": 8, "similarity_threshold": 0.5},
                cookies={"rag_session": cookie},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["name"] == "renamed"
            assert data["top_k"] == 8
            assert data["similarity_threshold"] == 0.5

            # DELETE 软删
            resp = await client.delete(f"/api/kb/{kb_id}", cookies={"rag_session": cookie})
            assert resp.status_code == 200
            # 已删除的知识库从列表消失、详情 404
            resp = await client.get("/api/kb", cookies={"rag_session": cookie})
            assert all(kb["id"] != kb_id for kb in resp.json()["data"])
            resp = await client.get(f"/api/kb/{kb_id}", cookies={"rag_session": cookie})
            assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_list_kb_includes_doc_count_and_chunk_total(
    migrated_mysql_url: str,
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)
        async with factory() as session:
            session.add(
                Document(
                    kb_id=kb_id,
                    owner_user_id=(await session.scalar(select(User.id).where(User.username == username))),
                    title="d1.txt",
                    source="d1.txt",
                    source_type="upload",
                    file_path="a/b/c.txt",
                    status="done",
                    chunk_count=12,
                )
            )
            await session.commit()

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username)
            resp = await client.get("/api/kb", cookies={"rag_session": cookie})

        assert resp.status_code == 200
        entry = next(kb for kb in resp.json()["data"] if kb["id"] == kb_id)
        assert entry["doc_count"] == 1
        assert entry["chunk_total"] == 12
    finally:
        await engine.dispose()


async def test_docs_upload_list_status_reindex_delete_flow(
    migrated_mysql_url: str, upload_root: str
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)

        app = create_app(_settings(migrated_mysql_url, upload_root), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username)

            # 上传
            resp = await client.post(
                f"/api/kb/{kb_id}/docs/upload",
                files={"file": ("hello.txt", b"hello world", "text/plain")},
                cookies={"rag_session": cookie},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["status"] == "pending"
            doc_id = data["doc_id"]
            assert data["job_id"] > 0

            # 列表
            resp = await client.get(f"/api/kb/{kb_id}/docs", cookies={"rag_session": cookie})
            assert resp.status_code == 200
            docs = resp.json()["data"]
            assert len(docs) == 1
            assert docs[0]["id"] == doc_id
            assert docs[0]["title"] == "hello.txt"
            assert docs[0]["source_type"] == "upload"

            # 状态（stage 来自最新 job）
            resp = await client.get(
                f"/api/kb/{kb_id}/docs/{doc_id}/status", cookies={"rag_session": cookie}
            )
            assert resp.status_code == 200
            detail = resp.json()["data"]
            assert detail["doc_id"] == doc_id
            assert detail["stage"] is None

            # chunks（向量库未落地，返回空）
            resp = await client.get(
                f"/api/kb/{kb_id}/docs/{doc_id}/chunks", cookies={"rag_session": cookie}
            )
            assert resp.status_code == 200
            assert resp.json()["data"] == {"total": 0, "items": []}

            # reindex
            resp = await client.post(
                f"/api/kb/{kb_id}/docs/{doc_id}/reindex", cookies={"rag_session": cookie}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["job_id"] > 0
            async with factory() as session:
                jobs = (await session.execute(
                    select(DocumentJob).where(DocumentJob.doc_id == doc_id)
                )).scalars().all()
                assert len(jobs) == 1
                assert jobs[0].job_type == "ingest"

            # 删除（软删）
            resp = await client.delete(
                f"/api/kb/{kb_id}/docs/{doc_id}", cookies={"rag_session": cookie}
            )
            assert resp.status_code == 200
            async with factory() as session:
                doc = (await session.execute(
                    select(Document).where(Document.id == doc_id)
                )).scalar_one()
                assert doc.status == "deleting"
            resp = await client.get(f"/api/kb/{kb_id}/docs", cookies={"rag_session": cookie})
            assert all(d["id"] != doc_id for d in resp.json()["data"])
    finally:
        await engine.dispose()


async def test_docs_endpoints_require_admin(
    migrated_mysql_url: str, upload_root: str
) -> None:
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)
        emp_username, _ = await _seed_admin(factory, role="user", prefix="emp")

        app = create_app(_settings(migrated_mysql_url, upload_root), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, emp_username)

            for method, url, kwargs in [
                ("post", f"/api/kb/{kb_id}/docs/upload", {"files": {"file": ("a.txt", b"x", "text/plain")}}),
                ("get", f"/api/kb/{kb_id}/docs", {}),
            ]:
                resp = await getattr(client, method)(url, cookies={"rag_session": cookie}, **kwargs)
                assert resp.status_code == 403, f"{method} {url}: {resp.text}"
    finally:
        await engine.dispose()


async def test_kb_test_endpoint_returns_empty_chunks(
    migrated_mysql_url: str,
) -> None:
    """检索模块为 stub：POST /api/kb/{id}/test 返回空数组并保留接口。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username)
            resp = await client.post(
                f"/api/kb/{kb_id}/test",
                json={"question": "测试"},
                cookies={"rag_session": cookie},
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []
    finally:
        await engine.dispose()


async def test_kb_reindex_flow(
    migrated_mysql_url: str,
) -> None:
    """重建索引：删旧 collection、切 v2、为非 deleting 文档建 ingest job。"""
    from unittest.mock import AsyncMock

    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, kb_id = await _seed_admin(factory)
        async with factory() as session:
            owner_id = await session.scalar(select(User.id).where(User.username == username))
            session.add_all([
                Document(
                    kb_id=kb_id, owner_user_id=owner_id, title="d1.txt", source="d1.txt",
                    source_type="upload", file_path="a/d1.txt", status="done",
                    chunk_count=5, ingested_at=None,
                ),
                Document(
                    kb_id=kb_id, owner_user_id=owner_id, title="d2.txt", source="d2.txt",
                    source_type="upload", file_path="a/d2.txt", status="failed",
                    chunk_count=0, error_message="boom", ingested_at=None,
                ),
                # 软删中的文档不参与重建
                Document(
                    kb_id=kb_id, owner_user_id=owner_id, title="gone.txt", source="gone.txt",
                    source_type="upload", file_path="a/gone.txt", status="deleting",
                    chunk_count=3,
                ),
            ])
            await session.commit()

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        # 测试不触发 startup：手动注入 mock chroma，断言 delete_collection 被调用
        app.state.ingest_chroma = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username)
            resp = await client.post(
                f"/api/kb/{kb_id}/reindex", cookies={"rag_session": cookie}
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {"job_count": 2}

        # delete_collection 以旧 collection 名为参数被调用一次（seed 建库时为 kb_seed_v1）
        app.state.ingest_chroma.delete_collection.assert_awaited_once_with("kb_seed_v1")

        async with factory() as session:
            kb = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )).scalar_one()
            assert kb.active_collection == f"kb_{kb_id}_v2"
            assert kb.index_version == 2
            assert kb.index_status == "rebuilding"

            # 每个非 deleting 文档：状态重置 + 新建 ingest job
            docs = (await session.execute(
                select(Document).where(Document.kb_id == kb_id)
            )).scalars().all()
            by_title = {d.title: d for d in docs}
            assert by_title["d1.txt"].status == "pending"
            assert by_title["d1.txt"].chunk_count == 0
            assert by_title["d2.txt"].status == "pending"
            assert by_title["d2.txt"].error_message is None
            # 软删文档不受影响
            assert by_title["gone.txt"].status == "deleting"

            jobs = (await session.execute(
                select(DocumentJob).where(DocumentJob.owner_user_id == owner_id)
            )).scalars().all()
            assert len(jobs) == 2
            assert all(j.job_type == "ingest" for j in jobs)
            assert {j.doc_id for j in jobs} == {by_title["d1.txt"].id, by_title["d2.txt"].id}
    finally:
        await engine.dispose()


async def test_delete_kb_removes_links_and_orphan_conversations(
    migrated_mysql_url: str,
) -> None:
    """删库后：只绑该库的会话整体删除（Message/Reference 级联），
    多库会话保留且关联只剩其余库、该库引用被删。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        username, _ = await _seed_admin(factory)
        async with factory() as session:
            owner_id = await session.scalar(select(User.id).where(User.username == username))
            kb_a = KnowledgeBase(
                owner_user_id=owner_id, name="kb-a", embedding_model="t",
                embedding_dimension=128, active_collection="a",
            )
            kb_b = KnowledgeBase(
                owner_user_id=owner_id, name="kb-b", embedding_model="t",
                embedding_dimension=128, active_collection="b",
            )
            session.add_all([kb_a, kb_b])
            await session.flush()
            id_a, id_b = kb_a.id, kb_b.id

            conv_x = Conversation(id=f"x-{uuid.uuid4().hex}", owner_user_id=owner_id)
            conv_y = Conversation(id=f"y-{uuid.uuid4().hex}", owner_user_id=owner_id)
            session.add_all([conv_x, conv_y])
            await session.flush()
            session.add_all([
                ConversationKb(conversation_id=conv_x.id, kb_id=id_a, owner_user_id=owner_id),
                ConversationKb(conversation_id=conv_x.id, kb_id=id_b, owner_user_id=owner_id),
                ConversationKb(conversation_id=conv_y.id, kb_id=id_a, owner_user_id=owner_id),
            ])
            await session.flush()

            # X: 有属于 A 和 B 的引用；Y: 有属于 A 的引用（走一次真实消息）
            msg_x = Message(
                conversation_id=conv_x.id, role="assistant", content="x",
                status="completed", sequence=0,
            )
            msg_y = Message(
                conversation_id=conv_y.id, role="assistant", content="y",
                status="completed", sequence=0,
            )
            session.add_all([msg_x, msg_y])
            await session.flush()
            session.add_all([
                Reference(message_id=msg_x.id, chunk_id="xa", doc_title="d", kb_id=id_a, kb_name="kb-a"),
                Reference(message_id=msg_x.id, chunk_id="xb", doc_title="d", kb_id=id_b, kb_name="kb-b"),
                Reference(message_id=msg_y.id, chunk_id="ya", doc_title="d", kb_id=id_a, kb_name="kb-a"),
            ])
            session.add(QueryLog(
                conversation_id=conv_x.id, owner_user_id=owner_id, kb_id=id_a,
                user_message_id=msg_x.id,
            ))
            await session.commit()
            cid_x, cid_y = conv_x.id, conv_y.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username)
            resp = await client.delete(f"/api/kb/{id_a}", cookies={"rag_session": cookie})
            assert resp.status_code == 200

        async with factory() as session:
            # Y（只绑 A）整体删除：会话及其 Message 均不存在
            assert await session.scalar(select(Conversation).where(Conversation.id == cid_y)) is None
            assert await session.scalar(
                select(func.count(Message.id)).where(Message.conversation_id == cid_y)
            ) == 0

            # X 保留，关联只剩 B；X 中 A 库引用被删、B 库引用保留
            assert await session.scalar(select(Conversation).where(Conversation.id == cid_x)) is not None
            links = (await session.execute(
                select(ConversationKb.kb_id).where(ConversationKb.conversation_id == cid_x)
            )).scalars().all()
            assert links == [id_b]
            refs = (await session.execute(
                select(Reference.chunk_id).where(Reference.message_id.in_(
                    select(Message.id).where(Message.conversation_id == cid_x)
                ))
            )).scalars().all()
            assert refs == ["xb"]

            # A 库关联行与 QueryLog 均已删除
            assert await session.scalar(select(func.count(ConversationKb.id)).where(
                ConversationKb.kb_id == id_a
            )) == 0
            assert await session.scalar(select(func.count(QueryLog.id)).where(
                QueryLog.kb_id == id_a
            )) == 0
    finally:
        await engine.dispose()
