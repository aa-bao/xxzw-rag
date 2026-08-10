"""流式源测试：根数组、$.posts[*]、根对象、JSONL、截断与畸形。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.structured.models import SourceRecord
from src.structured.paths import compile_path
from src.structured.stream import SourceSyntaxError, iter_source


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_root_array_streams_elements(tmp_path: Path) -> None:
    path = _write(tmp_path, "root-array.json", b'[{"id": 1}, {"id": 2}, {"id": 3}]')
    records = list(iter_source(path, "json", compile_path("$[*]")))
    assert [r.value for r in records] == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [r.source_pointer for r in records] == ["/0", "/1", "/2"]
    assert all(isinstance(r, SourceRecord) for r in records)


def test_nested_array_streams_elements(tmp_path: Path) -> None:
    payload = {"posts": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    path = _write(tmp_path, "nested.json", json.dumps(payload).encode())
    records = list(iter_source(path, "json", compile_path("$.posts[*]")))
    assert [r.value for r in records] == payload["posts"]
    assert [r.source_pointer for r in records] == ["/posts/0", "/posts/1", "/posts/2"]


def test_root_object_is_single_record(tmp_path: Path) -> None:
    payload = {"title": "hello", "body": "world"}
    path = _write(tmp_path, "object.json", json.dumps(payload).encode())
    records = list(iter_source(path, "json", compile_path("$")))
    assert len(records) == 1
    assert records[0].value == payload
    assert records[0].source_pointer == "$"


def test_jsonl_lines_are_records(tmp_path: Path) -> None:
    lines = [
        {"id": "a", "text": "first"},
        {"id": "b", "text": "second"},
    ]
    path = _write(
        tmp_path, "data.jsonl", ("\n".join(json.dumps(l) for l in lines) + "\n").encode()
    )
    records = list(iter_source(path, "jsonl", compile_path("$")))
    assert [r.value for r in records] == lines
    assert [r.source_pointer for r in records] == ["/line/1", "/line/2"]


def test_jsonl_scalar_values_are_records(tmp_path: Path) -> None:
    path = _write(tmp_path, "scalars.jsonl", b'42\n"str"\n[1,2]\n')
    records = list(iter_source(path, "jsonl", compile_path("$")))
    assert [r.value for r in records] == [42, "str", [1, 2]]


def test_empty_jsonl_yields_no_records(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.jsonl", b"")
    records = list(iter_source(path, "jsonl", compile_path("$")))
    assert records == []


def test_truncated_json_raises_source_syntax_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "truncated.json", b'{"posts": [{"id": 1}, {"id": 2')
    with pytest.raises(SourceSyntaxError):
        list(iter_source(path, "json", compile_path("$.posts[*]")))


def test_truncated_root_array_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "truncated-array.json", b'[{"a": 1}, {"b": 2')
    with pytest.raises(SourceSyntaxError):
        list(iter_source(path, "json", compile_path("$[*]")))


def test_malformed_jsonl_raises_source_syntax_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.jsonl", b'{"a": 1}\n{bad}\n')
    with pytest.raises(SourceSyntaxError):
        list(iter_source(path, "jsonl", compile_path("$")))


def test_malformed_jsonl_incomplete_last_line_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad2.jsonl", b'{"a": 1}\n{"b": 2')
    with pytest.raises(SourceSyntaxError):
        list(iter_source(path, "jsonl", compile_path("$")))


def test_malformed_json_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.json", b'{"a": 1, }')
    with pytest.raises(SourceSyntaxError):
        list(iter_source(path, "json", compile_path("$")))
