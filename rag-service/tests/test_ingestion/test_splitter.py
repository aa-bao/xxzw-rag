from __future__ import annotations

from src.ingestion.splitter import split_text


def test_chunk_ids_are_stable() -> None:
    first = split_text(7, "alpha beta", chunk_size=5, overlap=2)
    second = split_text(7, "alpha beta", chunk_size=5, overlap=2)
    assert first and second
    assert first[0]["chunk_id"] == second[0]["chunk_id"]
