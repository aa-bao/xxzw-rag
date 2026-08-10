from __future__ import annotations

import sqlite3
from pathlib import Path

from src.retrieval.fts import FtsIndex, StagedChunk


def _chunk(
    chunk_id: str,
    collection_id: str = "col1",
    run_id: str = "run1",
    title: str = "",
    keywords: str = "",
    content: str = "",
    metadata: dict | None = None,
) -> StagedChunk:
    return StagedChunk(
        chunk_id=chunk_id,
        title=title,
        keywords=keywords,
        content=content,
        collection_id=collection_id,
        ingest_run_id=run_id,
        metadata=metadata or {},
    )


def _stage(
    tmp_path: Path,
    chunks: list[StagedChunk],
) -> FtsIndex:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.set_expected(chunks)
    index.stage_chunks(chunks)
    return index


def _table_names(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _index_names(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _index_sql(db_path: Path, name: str) -> str | None:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
        ).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def test_initialize_creates_expected_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "retrieval.db"
    index = FtsIndex(db_path)
    index.initialize()

    assert {"fts_chunks", "chunk_meta", "chunk_filter"} <= _table_names(db_path)
    assert {"idx_chunk_meta_run", "idx_chunk_filter_field"} <= _index_names(db_path)

    connection = index.connection()
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        meta_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'chunk_meta'"
        ).fetchone()[0]
        filter_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'chunk_filter'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert "PRIMARY KEY" in meta_sql
    assert "value_type" in filter_sql
    assert "normalized_value" in filter_sql

    run_index_sql = _index_sql(db_path, "idx_chunk_meta_run")
    assert run_index_sql is not None
    assert "collection_id" in run_index_sql
    assert "ingest_run_id" in run_index_sql
    assert "index_state" in run_index_sql
    field_index_sql = _index_sql(db_path, "idx_chunk_filter_field")
    assert field_index_sql is not None
    assert "collection_id" in field_index_sql
    assert "field_name" in field_index_sql
    assert "normalized_value" in field_index_sql


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.initialize()
    assert {"fts_chunks", "chunk_meta", "chunk_filter"} <= _table_names(tmp_path / "retrieval.db")


def test_stage_chunks_replaces_same_chunk_ids(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.stage_chunks([_chunk("c1", content="first"), _chunk("c2", content="second")])

    index.stage_chunks([_chunk("c1", content="replaced"), _chunk("c3", content="third")])

    connection = sqlite3.connect(tmp_path / "retrieval.db")
    try:
        rows = connection.execute("SELECT chunk_id, content_hash FROM chunk_meta ORDER BY chunk_id").fetchall()
        fts = connection.execute("SELECT rowid, chunk_id, content FROM fts_chunks ORDER BY rowid").fetchall()
    finally:
        connection.close()
    assert [row[0] for row in rows] == ["c1", "c2", "c3"]
    # c1 content updated in place, no duplicate rows
    c1_rows = [row for row in fts if row[1] == "c1"]
    assert len(c1_rows) == 1
    assert c1_rows[0][2] == "replaced"
    from src.retrieval.fts import _content_hash
    assert rows[0][1] == _content_hash("", "", "replaced")


def test_activate_run_marks_one_active_and_others_dormant(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    run1 = [_chunk("a1", run_id="run1"), _chunk("a2", run_id="run1")]
    run2 = [_chunk("b1", run_id="run2"), _chunk("b2", run_id="run2")]
    index.stage_chunks(run1)
    index.stage_chunks(run2)

    index.activate_run("col1", "run1")
    index.activate_run("col1", "run2")

    connection = sqlite3.connect(tmp_path / "retrieval.db")
    try:
        rows = connection.execute(
            "SELECT chunk_id, index_state FROM chunk_meta ORDER BY chunk_id"
        ).fetchall()
    finally:
        connection.close()
    states = dict(rows)
    assert states["a1"] == "dormant"
    assert states["a2"] == "dormant"
    assert states["b1"] == "active"
    assert states["b2"] == "active"


def test_delete_run_removes_fts_and_meta_rows(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.stage_chunks([_chunk("a1", run_id="run1"), _chunk("b1", run_id="run2")])
    index.activate_run("col1", "run2")

    index.delete_run("col1", "run1")

    connection = sqlite3.connect(tmp_path / "retrieval.db")
    try:
        meta = connection.execute("SELECT chunk_id FROM chunk_meta ORDER BY chunk_id").fetchall()
        fts = connection.execute("SELECT chunk_id FROM fts_chunks ORDER BY chunk_id").fetchall()
    finally:
        connection.close()
    assert [row[0] for row in meta] == ["b1"]
    assert [row[0] for row in fts] == ["b1"]


def test_verify_run_reports_exact_ids_count_and_hash(tmp_path: Path) -> None:
    chunks = [
        _chunk("c1", title="title one", content="body one"),
        _chunk("c2", title="title two", content="body two"),
        _chunk("c3", title="title three", content="body three"),
    ]
    index = _stage(tmp_path, chunks)
    report = index.verify_run("col1", "run1")

    assert report["chunk_ids"] == {"c1", "c2", "c3"}
    assert report["count"] == 3
    assert len(report["aggregate_hash"]) == 64
    assert report["matches_expected"] is True


def test_verify_run_detects_missing_chunk(tmp_path: Path) -> None:
    chunks = [_chunk("c1", content="body one"), _chunk("c2", content="body two")]
    index = _stage(tmp_path, chunks)

    connection = sqlite3.connect(tmp_path / "retrieval.db")
    try:
        connection.execute("DELETE FROM chunk_meta WHERE chunk_id = 'c2'")
        connection.commit()
    finally:
        connection.close()

    report = index.verify_run("col1", "run1")
    assert report["count"] == 1
    assert report["chunk_ids"] == {"c1"}
    assert report["matches_expected"] is False


def test_verify_run_detects_content_drift(tmp_path: Path) -> None:
    chunks = [_chunk("c1", content="body one")]
    index = _stage(tmp_path, chunks)

    connection = sqlite3.connect(tmp_path / "retrieval.db")
    try:
        connection.execute("UPDATE chunk_meta SET content_hash = 'deadbeef' WHERE chunk_id = 'c1'")
        connection.commit()
    finally:
        connection.close()

    report = index.verify_run("col1", "run1")
    assert report["count"] == 1
    assert report["matches_expected"] is False


def test_search_only_returns_active_run_of_collection(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    run1 = [
        _chunk("a1", run_id="run1", title="alpha", content="免税"),
        _chunk("a2", run_id="run1", content="alpha"),
    ]
    run2 = [_chunk("b1", run_id="run2", title="alpha", content="免税")]
    index.stage_chunks(run1)
    index.stage_chunks(run2)
    index.activate_run("col1", "run2")

    hits = index.search("免税", "col1", limit=10)
    assert [hit.chunk_id for hit in hits] == ["b1"]
    for hit in hits:
        assert hit.lexical_rank == 1


def test_search_restricts_to_active_run_ids(tmp_path: Path) -> None:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.stage_chunks([_chunk("a1", run_id="run1", content="免税")])
    index.stage_chunks([_chunk("b1", run_id="run2", content="免税")])
    index.activate_run("col1", "run1")

    hits = index.search("免税", "col1", limit=10, active_run_ids=("run1",))
    assert [hit.chunk_id for hit in hits] == ["a1"]
    # 非活动 run 即使出现在候选 run 列表也不会被检索
    hits = index.search("免税", "col1", limit=10, active_run_ids=("run2",))
    assert hits == []


def test_search_hit_carries_metadata(tmp_path: Path) -> None:
    chunks = [_chunk("c1", content="免税", metadata={"category": "policy"})]
    index = _stage(tmp_path, chunks)
    index.activate_run("col1", "run1")

    hits = index.search("免税", "col1", limit=10)
    assert len(hits) == 1
    assert hits[0].metadata == {"category": "policy"}
