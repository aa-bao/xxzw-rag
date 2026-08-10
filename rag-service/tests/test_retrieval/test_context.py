from __future__ import annotations

from src.retrieval.context import pack_context
from src.retrieval.module import RetrievedChunk


def _chunk(chunk_id: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        content=content,
        doc_id=1,
        title="doc",
        page=None,
        score=0.5,
    )


def test_pack_context_preserves_order_and_removes_duplicates() -> None:
    chunks = [
        _chunk("a", "first"),
        _chunk("b", "second"),
        _chunk("a", "duplicate"),
        _chunk("c", "third"),
    ]

    packed = pack_context(chunks)

    assert [chunk.chunk_id for chunk in packed] == ["a", "b", "c"]
    assert [chunk.content for chunk in packed] == ["first", "second", "third"]


def test_pack_context_stops_at_eight_chunks() -> None:
    packed = pack_context([_chunk(str(index), "x") for index in range(10)])

    assert [chunk.chunk_id for chunk in packed] == [str(index) for index in range(8)]


def test_pack_context_allows_first_chunk_but_rejects_later_token_overflow() -> None:
    first = _chunk("large", "中" * 2100)
    second = _chunk("later", "small")

    packed = pack_context([first, second], max_tokens=2000)

    assert [chunk.chunk_id for chunk in packed] == ["large"]


def test_pack_context_skips_empty_content_before_allowing_first_chunk() -> None:
    packed = pack_context([_chunk("empty", ""), _chunk("first", "中" * 2100)])

    assert [chunk.chunk_id for chunk in packed] == ["first"]
