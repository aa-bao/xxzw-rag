from __future__ import annotations

import json
from types import SimpleNamespace

from src.ingestion.worker import IngestWorker
from src.structured.models import FieldMapping, MappingDefinition, RecordTypeMapping


def test_structured_chunks_map_root_array_and_children(tmp_path) -> None:
    source = tmp_path / "records.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "post-1",
                    "title": "First post",
                    "body": "Parent body",
                    "comments": [{"id": "comment-1", "content": "Child body"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    definition = MappingDefinition(
        source_format="json",
        record_types=(
            RecordTypeMapping(
                name="post",
                record_path="$",
                fields=(
                    FieldMapping(path="id", role="id"),
                    FieldMapping(path="title", role="title"),
                    FieldMapping(path="body", role="content"),
                ),
                children=(
                    RecordTypeMapping(
                        name="comment",
                        record_path="comments[*]",
                        fields=(
                            FieldMapping(path="id", role="id"),
                            FieldMapping(path="content", role="content"),
                        ),
                    ),
                ),
            ),
        ),
    )
    worker = object.__new__(IngestWorker)
    worker._upload_root = tmp_path
    document = SimpleNamespace(id=7, file_path="records.json", content_hash=None)

    chunks = worker._structured_chunks(document, definition, 13, 256, 50)

    assert [chunk["record_type"] for chunk in chunks] == ["post", "comment"]
    assert chunks[0]["record_id"] == "post-1"
    assert chunks[1]["record_id"] == "comment-1"
    assert chunks[1]["parent_id"] == "post-1"
    assert chunks[1]["mapping_version_id"] == 13
    assert chunks[0]["chunk_id"].startswith("7:post-1:0:")


def test_structured_chunks_do_not_index_parent_only_children(tmp_path) -> None:
    source = tmp_path / "record.json"
    source.write_text(
        json.dumps({"id": "post-1", "body": "Parent", "comments": [{"body": "Child"}]}),
        encoding="utf-8",
    )
    definition = MappingDefinition(
        source_format="json",
        record_types=(
            RecordTypeMapping(
                name="post",
                record_path="$",
                fields=(
                    FieldMapping(path="id", role="id"),
                    FieldMapping(path="body", role="content"),
                ),
                children=(
                    RecordTypeMapping(
                        name="comment",
                        record_path="comments[*]",
                        fields=(FieldMapping(path="body", role="content"),),
                        chunk_policy="parent-only",
                    ),
                ),
            ),
        ),
    )
    worker = object.__new__(IngestWorker)
    worker._upload_root = tmp_path
    document = SimpleNamespace(id=8, file_path="record.json", content_hash=None)

    chunks = worker._structured_chunks(document, definition, 14, 256, 50)

    assert [chunk["record_type"] for chunk in chunks] == ["post"]
