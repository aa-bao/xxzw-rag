from __future__ import annotations

import json
import uuid
from collections import deque

import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from src.api.app import create_app
from src.auth.passwords import PasswordHasher
from src.db.models import Conversation, ConversationKb, KnowledgeBase, Message, QueryLog, Reference, User
from src.db.session import create_engine, create_session_factory
from src.retrieval.module import RetrievedChunk
from src.shared.config import Settings
from test_retrieval.fakes import FakeRetrieval


class _FakeChatTransport(httpx.AsyncBaseTransport):
    """模拟模型中转：POST /chat/completions 返回 OpenAI 风格 SSE 流。"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = b"data: " + json.dumps(
            {"choices": [{"delta": {"content": "answer from model"}}]}
        ).encode() + b"\n\ndata: [DONE]\n\n"
        stream = httpx.ByteStream(body)
        return httpx.Response(200, stream=stream, request=request)


def _stub_model_relay(app) -> None:
    """把模型中转的 HTTP client 换成假传输，避免真实网络调用。"""
    app.state.model_relay_client._client = httpx.AsyncClient(transport=_FakeChatTransport())


def _settings(database_url: str) -> Settings:
    return Settings.model_validate(
        {
            "app": {"browser_origin": "http://127.0.0.1:8000", "secure_cookie": False},
            "rag": {
                "chroma_mode": "persist",
                "chroma_persist_dir": "./data/chroma",
                "default_chunk_size": 64,
                "default_overlap": 2,
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
                "root_dir": "./data/uploads",
                "temp_dir": "./data/tmp",
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


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.cookies["rag_session"]


async def test_conversation_cannot_use_other_users_kb(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    a = f"alice-{uuid.uuid4().hex[:8]}"
    b = f"bob-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            ua = User(username=a, password_hash=hasher.hash("s"), role="user", status="active")
            ub = User(username=b, password_hash=hasher.hash("s"), role="user", status="active")
            session.add_all([ua, ub])
            await session.flush()
            kb_b = KnowledgeBase(
                owner_user_id=ub.id, name="b-kb",
                embedding_model="t", embedding_dimension=128, active_collection="x",
            )
            session.add(kb_b)
            await session.commit()
            bob_kb = kb_b.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, a, "s")
            resp = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [bob_kb]},
                cookies={"rag_session": cookie},
            )

        assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_query_schema_rejects_client_history_and_kb(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.flush()
            conv = Conversation(id=uuid.uuid4().hex, owner_user_id=u.id)
            session.add(conv)
            session.add(ConversationKb(conversation_id=conv.id, kb_id=kb.id, owner_user_id=u.id))
            await session.commit()
            cid = conv.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            resp = await client.post(
                "/api/chat/query",
                json={
                    "conversation_id": cid, "question": "x",
                    "kb_id": 2, "history": [],
                },
                cookies={"rag_session": cookie},
            )

        assert resp.status_code == 422
    finally:
        await engine.dispose()


async def test_create_conversation_and_stream_answer(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")

            cr = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [kb_id]},
                cookies={"rag_session": cookie},
            )
            assert cr.status_code == 200
            cid = cr.json()["data"]["id"]

            qr = await client.post(
                "/api/chat/query",
                json={"conversation_id": cid, "question": "测试"},
                cookies={"rag_session": cookie},
            )
            assert qr.status_code == 200
            body = qr.text
            assert "event: chunk" in body or "event: done" in body

        # List conversations
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            lr = await client.get("/api/chat/history", cookies={"rag_session": cookie})
            assert lr.status_code == 200
            assert len(lr.json()["data"]) == 1
    finally:
        await engine.dispose()


async def test_query_auto_titles_and_answers_without_chroma(
    migrated_mysql_url: str,
) -> None:
    """First question becomes the conversation title (truncated to 20 chars +
    ellipsis); answer streams with references even when the vector store is
    not wired up."""
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            cr = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [kb_id]},
                cookies={"rag_session": cookie},
            )
            cid = cr.json()["data"]["id"]

            long_question = "这是一句超过二十个字的用户问题，用于验证标题截断逻辑"
            qr = await client.post(
                "/api/chat/query",
                json={"conversation_id": cid, "question": long_question},
                cookies={"rag_session": cookie},
            )
            assert qr.status_code == 200
            body = qr.text
            # Empty-retrieval path: chunk (empty response) + done, no LLM call
            assert "event: chunk" in body
            assert "event: done" in body
            assert "event: references" not in body

            hr = await client.get("/api/chat/history", cookies={"rag_session": cookie})
            assert hr.status_code == 200
            entries = hr.json()["data"]
            assert len(entries) == 1
            assert entries[0]["title"] == long_question[:20] + "…"
            assert entries[0]["kb_name"] == "k"

            mr = await client.get(f"/api/chat/conversations/{cid}/messages", cookies={"rag_session": cookie})
            assert mr.status_code == 200
            msgs = mr.json()["data"]
            assert [m["role"] for m in msgs] == ["user", "assistant"]
            assert msgs[0]["content"] == long_question
            assert msgs[1]["status"] == "completed"
            assert msgs[1]["references"] == []

        # Messages, references and query logs are cascade-deleted with the conversation
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            dr = await client.delete(f"/api/chat/conversations/{cid}", cookies={"rag_session": cookie})
            assert dr.status_code == 200
            nr = await client.get(f"/api/chat/conversations/{cid}/messages", cookies={"rag_session": cookie})
            assert nr.status_code == 404
            hr = await client.get("/api/chat/history", cookies={"rag_session": cookie})
            assert hr.json()["data"] == []

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count(Message.id)).where(Message.conversation_id == cid)
                )
            ) == 0
            assert (
                await session.scalar(
                    select(func.count(QueryLog.id)).where(QueryLog.conversation_id == cid)
                )
            ) == 0
    finally:
        await engine.dispose()


async def test_messages_return_references_and_leave_streaming(
    migrated_mysql_url: str,
) -> None:
    """References are returned per assistant message; residual 'streaming'
    rows are marked failed and excluded."""
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.flush()
            conv = Conversation(id=uuid.uuid4().hex, owner_user_id=u.id)
            session.add(conv)
            session.add(ConversationKb(conversation_id=conv.id, kb_id=kb.id, owner_user_id=u.id))
            await session.flush()
            user_msg = Message(
                conversation_id=conv.id, role="user", content="hi",
                status="completed", sequence=0,
            )
            assistant_msg = Message(
                conversation_id=conv.id, role="assistant", content="answer",
                status="completed", sequence=1,
            )
            residual = Message(
                conversation_id=conv.id, role="assistant", content=None,
                status="streaming", sequence=2,
            )
            session.add_all([user_msg, assistant_msg, residual])
            await session.flush()
            session.add(
                Reference(
                    message_id=assistant_msg.id,
                    chunk_id="c1", doc_id=7, doc_title="doc",
                    snippet="snippet", score=0.91, page=2,
                )
            )
            await session.commit()
            cid = conv.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            mr = await client.get(
                f"/api/chat/conversations/{cid}/messages", cookies={"rag_session": cookie}
            )
            assert mr.status_code == 200
            msgs = mr.json()["data"]
            assert len(msgs) == 2  # residual streaming row excluded
            assert msgs[1]["references"] == [
                {
                    "chunk_id": "c1",
                    "doc_id": 7,
                    "title": "doc",
                    "kb_id": None,
                    "kb_name": None,
                    "snippet": "snippet",
                    "score": 0.91,
                    "page": 2,
                    "is_neighbor": False,
                }
            ]

        async with factory() as session:
            residual_now = await session.scalar(
                select(Message).where(Message.id == residual.id)
            )
            assert residual_now.status == "failed"
    finally:
        await engine.dispose()


async def test_query_returns_empty_response_when_all_chunks_below_threshold(
    migrated_mysql_url: str,
) -> None:
    """KB similarity_threshold is forwarded to retrieval: chunks below it are
    filtered out and the engine falls back to the empty response."""
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id,
                name="k",
                embedding_model="t",
                embedding_dimension=128,
                active_collection="x",
                similarity_threshold=0.7,
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        retrieval = FakeRetrieval(
            [
                RetrievedChunk("c1", "content one", 1, "doc", None, 0.4, kb_id=kb_id),
                RetrievedChunk("c2", "content two", 1, "doc", None, 0.3, kb_id=kb_id),
            ]
        )
        app.state.ingest_chroma = retrieval
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            cr = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [kb_id]},
                cookies={"rag_session": cookie},
            )
            cid = cr.json()["data"]["id"]

            qr = await client.post(
                "/api/chat/query",
                json={"conversation_id": cid, "question": "测试"},
                cookies={"rag_session": cookie},
            )
            assert qr.status_code == 200
            body = qr.text
            # 低于阈值的 chunk 全部被过滤 → empty_response 兜底，无 LLM 调用
            assert "event: chunk" in body
            assert '"content": "empty"' in body
            assert "event: references" not in body

            assert retrieval.last_threshold == 0.7
            assert retrieval.last_expand_query == ""
            assert retrieval.last_expand_core_ids == []

        # 空检索路径也持久化了 query log
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count(QueryLog.id)).where(QueryLog.kb_id == kb_id)
                )
            ) == 1
    finally:
        await engine.dispose()


async def test_create_conversation_rejects_duplicate_and_empty_kb_ids(
    migrated_mysql_url: str,
) -> None:
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb = KnowledgeBase(
                owner_user_id=u.id, name="k", embedding_model="t",
                embedding_dimension=128, active_collection="x",
            )
            session.add(kb)
            await session.commit()
            kb_id = kb.id

        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")

            dup = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [kb_id, kb_id]},
                cookies={"rag_session": cookie},
            )
            assert dup.status_code == 422
            assert dup.json()["error"]["code"] == "KB_DUPLICATED"

            empty = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": []},
                cookies={"rag_session": cookie},
            )
            assert empty.status_code == 422

            many = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": list(range(1, 12))},
                cookies={"rag_session": cookie},
            )
            assert many.status_code == 422

            # 任一库非本人拥有 → 整体 404
            other = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [kb_id, 999999]},
                cookies={"rag_session": cookie},
            )
            assert other.status_code == 404
            assert other.json()["error"]["code"] == "KB_NOT_FOUND"
    finally:
        await engine.dispose()


async def test_multi_kb_conversation_merges_retrieval_and_reports_kb(
    migrated_mysql_url: str,
) -> None:
    """两个库分别检索，按 score 降序合并截断；references 带 kb 快照。"""
    hasher = PasswordHasher()
    username = f"alice-{uuid.uuid4().hex[:8]}"
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=username, password_hash=hasher.hash("s"), role="user", status="active")
            session.add(u)
            await session.flush()
            kb_a = KnowledgeBase(
                owner_user_id=u.id, name="kb-a", embedding_model="t",
                embedding_dimension=128, active_collection="a", top_k=3,
            )
            kb_b = KnowledgeBase(
                owner_user_id=u.id, name="kb-b", embedding_model="t",
                embedding_dimension=128, active_collection="b", top_k=3,
            )
            session.add_all([kb_a, kb_b])
            await session.commit()
            id_a, id_b = kb_a.id, kb_b.id

        # 库 A: 0.9/0.7，库 B: 0.95/0.5，top_k=3 → 合并后取 [0.95, 0.9, 0.7]
        app = create_app(_settings(migrated_mysql_url), session_factory=factory)
        _stub_model_relay(app)
        retrieval = FakeRetrieval(
            [
                RetrievedChunk("a1", "from a", 1, "doc a", None, 0.9, kb_id=id_a, kb_name="kb-a"),
                RetrievedChunk("a2", "from a", 1, "doc a", None, 0.7, kb_id=id_a, kb_name="kb-a"),
                RetrievedChunk("b1", "from b", 1, "doc b", None, 0.95, kb_id=id_b, kb_name="kb-b"),
                RetrievedChunk("b2", "from b", 1, "doc b", None, 0.5, kb_id=id_b, kb_name="kb-b"),
            ]
        )
        app.state.ingest_chroma = retrieval
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000",
        ) as client:
            cookie = await _login(client, username, "s")
            cr = await client.post(
                "/api/chat/conversations",
                json={"kb_ids": [id_a, id_b]},
                cookies={"rag_session": cookie},
            )
            assert cr.status_code == 200
            data = cr.json()["data"]
            assert data["kb_ids"] == [id_a, id_b]
            assert data["kb_id"] == id_a  # 兼容字段为首库
            cid = data["id"]

            qr = await client.post(
                "/api/chat/query",
                json={"conversation_id": cid, "question": "测试"},
                cookies={"rag_session": cookie},
            )
            assert qr.status_code == 200
            body = qr.text
            assert "event: references" in body
            refs = json.loads(
                body.split("event: references\ndata: ", 1)[1].split("\n\n", 1)[0]
            )["items"]
            # 按 score 降序整体截断 top_k=3
            assert [r["score"] for r in refs] == [0.95, 0.9, 0.7]
            assert [r["kb_name"] for r in refs] == ["kb-b", "kb-a", "kb-a"]
            assert [r["kb_id"] for r in refs] == [id_b, id_a, id_a]

            assert retrieval.last_kb_ids == [id_a, id_b]

            # 历史列表携带 kb_ids/kb_names
            hr = await client.get("/api/chat/history", cookies={"rag_session": cookie})
            entry = hr.json()["data"][0]
            assert entry["kb_ids"] == [id_a, id_b]
            assert entry["kb_names"] == ["kb-a", "kb-b"]

        # DB 断言：QueryLog 记首个库；Reference 带 kb 快照（只查本会话）
        async with factory() as session:
            log = await session.scalar(select(QueryLog).where(QueryLog.conversation_id == cid))
            assert log.kb_id == id_a
            refs = (await session.execute(
                select(Reference).where(
                    Reference.message_id.in_(
                        select(Message.id).where(Message.conversation_id == cid)
                    )
                )
            )).scalars().all()
            assert {r.kb_id for r in refs} == {id_a, id_b}
            assert all(r.kb_name is not None for r in refs)
    finally:
        await engine.dispose()
