from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
  chunk_id UNINDEXED, title, keywords, content,
  tokenize='unicode61 remove_diacritics 2'
);
"""

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_meta (
  chunk_id TEXT NOT NULL PRIMARY KEY,
  collection_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  index_state TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_meta_run
  ON chunk_meta (collection_id, ingest_run_id, index_state);
"""

_FILTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunk_filter (
  chunk_id TEXT NOT NULL,
  collection_id TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL,
  index_state TEXT NOT NULL,
  field_name TEXT NOT NULL,
  value_type TEXT NOT NULL,
  normalized_value TEXT NOT NULL,
  numeric_value REAL,
  datetime_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_filter_field
  ON chunk_filter (collection_id, field_name, normalized_value);
"""


@dataclass(frozen=True)
class StagedChunk:
    """One staging batch item. ``metadata`` maps filterable field names to values."""

    chunk_id: str
    title: str
    keywords: str
    content: str
    collection_id: str
    ingest_run_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FtsHit:
    chunk_id: str
    lexical_score: float
    lexical_rank: int
    metadata: dict[str, Any] = field(default_factory=dict)


class FtsIndex:
    """Persistent SQLite FTS5 index with run-based lifecycle.

    Each blocking method opens one short-lived ``sqlite3.Connection``;
    callers are expected to run them through ``asyncio.to_thread``. Run
    metadata lives in ordinary tables linked to ``fts_chunks`` by rowid.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    def connection(self) -> sqlite3.Connection:
        """Open a configured connection: WAL journal, foreign keys enabled.

        The calling method owns this connection and must close it.
        """
        if self._db_path != ":memory:":
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        connection = sqlite3.connect(self._db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(_FTS_SCHEMA)
        connection.executescript(_META_SCHEMA)
        connection.executescript(_FILTER_SCHEMA)

    def initialize(self) -> None:
        """Create tables and indexes if they do not exist. Idempotent."""
        connection = self.connection()
        try:
            self._initialize(connection)
            connection.commit()
        finally:
            connection.close()

    def stage_chunks(self, chunks: list[StagedChunk]) -> None:
        """Insert one staging batch; wraps the batch in a single transaction.

        Deletes any earlier staged rows with the same chunk IDs first, so a
        batch is replaceable.
        """
        connection = self.connection()
        try:
            self._initialize(connection)
            with connection:  # one transaction for the whole batch
                for chunk in chunks:
                    connection.execute(
                        "DELETE FROM fts_chunks WHERE chunk_id = ?", (chunk.chunk_id,)
                    )
                    connection.execute(
                        "DELETE FROM chunk_meta WHERE chunk_id = ?", (chunk.chunk_id,)
                    )
                    connection.execute(
                        "DELETE FROM chunk_filter WHERE chunk_id = ?", (chunk.chunk_id,)
                    )
                    cursor = connection.execute(
                        "INSERT INTO chunk_meta "
                        "(chunk_id, collection_id, ingest_run_id, index_state, "
                        " content_hash, metadata_json) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            chunk.chunk_id,
                            chunk.collection_id,
                            chunk.ingest_run_id,
                            "staged",
                            _content_hash(chunk.title, chunk.keywords, chunk.content),
                            json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True, default=_json_default),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO fts_chunks "
                        "(rowid, chunk_id, title, keywords, content) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (cursor.lastrowid, chunk.chunk_id, chunk.title, chunk.keywords, chunk.content),
                    )
                    for field_name, value in chunk.metadata.items():
                        if isinstance(value, (dict, list)):
                            continue  # 嵌套对象不可过滤，跳过
                        value_type, normalized = _normalize_filter_value(value)
                        numeric_value = float(normalized) if value_type == "number" else None
                        datetime_value = normalized if value_type == "datetime" else None
                        connection.execute(
                            "INSERT INTO chunk_filter "
                            "(chunk_id, collection_id, ingest_run_id, index_state, "
                            " field_name, value_type, normalized_value, "
                            " numeric_value, datetime_value) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                chunk.chunk_id,
                                chunk.collection_id,
                                chunk.ingest_run_id,
                                "staged",
                                field_name,
                                value_type,
                                normalized,
                                numeric_value,
                                datetime_value,
                            ),
                        )
        finally:
            connection.close()

    def activate_run(self, collection_id: str, ingest_run_id: str) -> None:
        """Mark one run active; other runs of the same collection go dormant.

        Rowid linkage keeps the FTS rows untouched: only ``chunk_meta``
        transitions.
        """
        connection = self.connection()
        try:
            self._initialize(connection)
            with connection:
                connection.execute(
                    "UPDATE chunk_meta SET index_state = 'dormant' "
                    "WHERE collection_id = ? AND index_state = 'active'",
                    (collection_id,),
                )
                connection.execute(
                    "UPDATE chunk_filter SET index_state = 'dormant' "
                    "WHERE collection_id = ? AND index_state = 'active'",
                    (collection_id,),
                )
                connection.execute(
                    "UPDATE chunk_meta SET index_state = 'active' "
                    "WHERE collection_id = ? AND ingest_run_id = ? AND index_state = 'staged'",
                    (collection_id, ingest_run_id),
                )
                connection.execute(
                    "UPDATE chunk_filter SET index_state = 'active' "
                    "WHERE collection_id = ? AND ingest_run_id = ? AND index_state = 'staged'",
                    (collection_id, ingest_run_id),
                )
        finally:
            connection.close()

    def delete_run(self, collection_id: str, ingest_run_id: str) -> None:
        """Remove every row of a run from both the FTS and the meta table."""
        connection = self.connection()
        try:
            self._initialize(connection)
            with connection:
                for row in connection.execute(
                    "SELECT rowid FROM chunk_meta "
                    "WHERE collection_id = ? AND ingest_run_id = ?",
                    (collection_id, ingest_run_id),
                ):
                    connection.execute("DELETE FROM fts_chunks WHERE rowid = ?", (row["rowid"],))
                    connection.execute(
                        "DELETE FROM chunk_filter WHERE chunk_id IN ("
                        "SELECT chunk_id FROM chunk_meta WHERE rowid = ?)",
                        (row["rowid"],),
                    )
                connection.execute(
                    "DELETE FROM chunk_meta WHERE collection_id = ? AND ingest_run_id = ?",
                    (collection_id, ingest_run_id),
                )
        finally:
            connection.close()

    def verify_run(self, collection_id: str, ingest_run_id: str) -> dict[str, Any]:
        """Compare the stored run against the expected staging batch.

        Returns a report with the exact chunk-ID set, the count, and the
        aggregate content hash (per-chunk SHA-256 digests hashed together),
        plus whether the persisted state matches the expected snapshot.
        """
        connection = self.connection()
        try:
            self._initialize(connection)
            rows = connection.execute(
                "SELECT chunk_id, content_hash FROM chunk_meta "
                "WHERE collection_id = ? AND ingest_run_id = ?",
                (collection_id, ingest_run_id),
            ).fetchall()
        finally:
            connection.close()


        chunk_ids = {row["chunk_id"] for row in rows}
        expected_ids = {chunk.chunk_id for chunk in self._expected_chunks or ()}
        report = {
            "chunk_ids": chunk_ids,
            "count": len(chunk_ids),
            "aggregate_hash": _aggregate_hash(sorted(row["content_hash"] for row in rows)),
        }
        report["matches_expected"] = (
            chunk_ids == expected_ids
            and report["count"] == len(expected_ids)
            and report["aggregate_hash"] == self._expected_hash
        )
        return report


    def search(
        self,
        query_text: str,
        collection_id: str,
        *,
        limit: int,
        active_run_ids: tuple[str, ...] | None = None,
        filter_chunk_ids: set[str] | None = None,
    ) -> list[FtsHit]:
        """BM25 search restricted to the active run of a collection.

        ``filter_chunk_ids`` (from :meth:`filter_chunk_ids`) narrows the
        candidate rows; an empty set short-circuits with no results. The
        SQLite distance is lower-is-better; ``lexical_score`` is its negation
        and ``lexical_rank`` starts at 1 for the closest hit.
        """
        if filter_chunk_ids == set():
            return []
        connection = self.connection()
        try:
            self._initialize(connection)
            where = "fts_chunks MATCH ? AND m.collection_id = ? AND m.index_state = 'active'"
            parameters: list[Any] = [query_text, collection_id]
            if active_run_ids:
                placeholders = ", ".join("?" for _ in active_run_ids)
                where += f" AND m.ingest_run_id IN ({placeholders})"
                parameters.extend(active_run_ids)
            if filter_chunk_ids is not None:
                placeholders = ", ".join("?" for _ in filter_chunk_ids)
                where += f" AND m.chunk_id IN ({placeholders})"
                parameters.extend(filter_chunk_ids)
            rows = connection.execute(
                "SELECT m.chunk_id, "
                "bm25(fts_chunks, 0.0, 5.0, 3.0, 1.0) AS distance, "
                "m.metadata_json "
                "FROM fts_chunks "
                "JOIN chunk_meta m ON m.rowid = fts_chunks.rowid "
                f"WHERE {where} "
                "ORDER BY distance ASC "
                "LIMIT ?",
                [*parameters, limit],
            ).fetchall()
        finally:
            connection.close()
        return [
            FtsHit(
                chunk_id=row["chunk_id"],
                lexical_score=-float(row["distance"]),
                lexical_rank=index + 1,
                metadata=_load_metadata(row["metadata_json"]),
            )
            for index, row in enumerate(rows)
        ]


    def filter_chunk_ids(
        self,
        collection_id: str,
        active_run_ids: tuple[str, ...],
        filters: tuple[Any, ...],
    ) -> set[str] | None:
        """See :mod:`src.retrieval.filters`; implemented there via this index.

        ``None`` means no filter is applied; ``set()`` means the filters match
        nothing.
        """
        from src.retrieval.filters import filter_chunk_ids

        return filter_chunk_ids(self, collection_id, active_run_ids, filters)

    # ------------------------------------------------------------------ #
    # Expected-snapshot support for verify_run (set by staging helpers)   #
    # ------------------------------------------------------------------ #

    _expected_chunks: list[StagedChunk] | None = None
    _expected_hash: str | None = None

    def set_expected(self, chunks: list[StagedChunk]) -> None:
        """Record the snapshot that ``verify_run`` must compare against."""
        self._expected_chunks = list(chunks)
        self._expected_hash = _aggregate_hash(
            sorted(_content_hash(c.title, c.keywords, c.content) for c in chunks)
        )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(
        f"metadata value of type {type(value).__name__} is not JSON serializable"
    )


def _normalize_filter_value(value: Any) -> tuple[str, str]:
    from src.retrieval.filters import normalize_filter_value

    return normalize_filter_value(value)


def _content_hash(title: str, keywords: str, content: str) -> str:
    digest = hashlib.sha256()
    digest.update(title.encode("utf-8"))
    digest.update(bytes([0]))
    digest.update(keywords.encode("utf-8"))
    digest.update(bytes([0]))
    digest.update(content.encode("utf-8"))
    return digest.hexdigest()


def _aggregate_hash(hashes: list[str]) -> str:
    digest = hashlib.sha256()
    for item in hashes:
        digest.update(item.encode("utf-8"))
    return digest.hexdigest()


def _load_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}
