from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from src.db.models import Conversation, ConversationKb, KnowledgeBase, Message, QueryLog, Reference, User
from src.db.session import create_engine, create_session_factory
from src.engine.query import QueryEngine
from src.retrieval.module import RetrievedChunk
from test_retrieval.fakes import FakeChatClient, FakeRetrieval


async def test_run_retrieves_from_all_kbs_and_merges(
    migrated_mysql_url: str,
) -> None:
    """QueryEngine.run 对每个绑定库分别检索，按 score 降序合并截断，
    Reference 带 kb 快照、QueryLog 记首个库。"""
    engine = create_engine(migrated_mysql_url, pool_size=2)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            u = User(username=f"u-{uuid.uuid4().hex[:8]}", password_hash="h", role="user", status="active")
            session.add(u)
            await session.flush()
            kb_a = KnowledgeBase(
                owner_user_id=u.id, name="kb-a", embedding_model="t",
                embedding_dimension=128, active_collection="a",
            )
            kb_b = KnowledgeBase(
                owner_user_id=u.id, name="kb-b", embedding_model="t",
                embedding_dimension=128, active_collection="b",
            )
            session.add_all([kb_a, kb_b])
            await session.flush()
            conv = Conversation(id=uuid.uuid4().hex, owner_user_id=u.id)
            session.add(conv)
            session.add_all([
                ConversationKb(conversation_id=conv.id, kb_id=kb_a.id, owner_user_id=u.id),
                ConversationKb(conversation_id=conv.id, kb_id=kb_b.id, owner_user_id=u.id),
            ])
            await session.commit()
            cid, uid = conv.id, u.id
            id_a, id_b = kb_a.id, kb_b.id

        retrieval = FakeRetrieval(
            [
                RetrievedChunk("a1", "from a", 1, "doc a", None, 0.6, kb_id=id_a, kb_name="kb-a"),
                RetrievedChunk("b1", "from b", 1, "doc b", None, 0.9, kb_id=id_b, kb_name="kb-b"),
            ]
        )
        chat = FakeChatClient()
        engine_q = QueryEngine(retrieval, chat)

        async with factory() as session:
            events = [
                event async for event in engine_q.run(
                    session,
                    conversation_id=cid,
                    question="question",
                    user_id=uid,
                    kb_ids=[id_a, id_b],
                    top_k=5,
                )
            ]

        assert any("event: references" in e for e in events)
        refs_event = next(e for e in events if "event: references" in e)
        items = json.loads(refs_event.split("\ndata: ", 1)[1])["items"]
        # 两库合并后按 score 降序
        assert [i["score"] for i in items] == [0.9, 0.6]
        assert [i["kb_name"] for i in items] == ["kb-b", "kb-a"]
        assert retrieval.last_kb_ids == [id_a, id_b]

        async with factory() as session:
            log = await session.scalar(select(QueryLog).where(QueryLog.conversation_id == cid))
            assert log.kb_id == id_a
            # 只查本会话的引用（MySQL 容器跨测试复用，不能全表断言）
            refs = (await session.execute(
                select(Reference).where(
                    Reference.message_id.in_(
                        select(Message.id).where(Message.conversation_id == cid)
                    )
                )
            )).scalars().all()
            assert {r.kb_id for r in refs} == {id_a, id_b}
            assert {r.kb_name for r in refs} == {"kb-a", "kb-b"}
    finally:
        await engine.dispose()
