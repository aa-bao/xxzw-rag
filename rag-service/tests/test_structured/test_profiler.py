"""结构探查器测试：统计、脱敏、确定性采样、指纹与兼容性。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.structured.profiler import (
    Compatibility,
    compatibility,
    profile_source,
    schema_fingerprint,
)
from src.structured.stream import SourceSyntaxError


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _json_file(tmp_path: Path, name: str, records: list[dict]) -> Path:
    return _write(tmp_path, name, json.dumps(records).encode())


def test_profile_counts_records_and_path_stats(tmp_path: Path) -> None:
    path = _json_file(
        tmp_path,
        "posts.json",
        [
            {"id": "a1", "body": "很长很长的正文内容 " * 20, "tags": ["x", "y"], "date": "2026-01-02T10:00:00Z"},
            {"id": "a2", "body": "另一段正文内容", "tags": ["x"], "date": "2026-01-03T11:30:00+08:00"},
        ],
    )
    profile = profile_source(path, "json")

    assert profile.record_count == 2
    assert profile.source_format == "json"

    id_stats = profile.stats("id")
    assert id_stats is not None
    assert id_stats.container == "scalar"
    assert id_stats.uniqueness == 1.0
    assert id_stats.null_rate == 0.0

    body = profile.stats("body")
    assert body is not None
    assert body.avg_length > 50
    assert body.length_min <= body.avg_length <= body.length_max

    date = profile.stats("date")
    assert date is not None
    assert date.datetime_parsed == 2
    assert date.datetime_parse_rate == 1.0

    tags = profile.stats("tags")
    assert tags is not None
    assert tags.container == "array"
    assert tags.element_count == 3  # x,y + x → 3 个标量元素
    assert tags.uniqueness == pytest.approx(2 / 3)
    assert tags.child_array_count == 0


def test_examples_are_redacted_and_capped(tmp_path: Path) -> None:
    path = _json_file(
        tmp_path,
        "secret.json",
        [{"secret": "S" * 500, "small": "ok"}],
    )
    profile = profile_source(path, "json")

    secret_stats = profile.stats("secret")
    assert secret_stats is not None
    assert len(secret_stats.examples) == 1
    assert len(secret_stats.examples[0]) == 200  # 截断到 200 字符
    assert "S" * 500 not in "".join(secret_stats.examples)

    small = profile.stats("small")
    assert small is not None
    assert small.examples == ["ok"]


def test_child_array_stats_aggregate_without_indices(tmp_path: Path) -> None:
    path = _json_file(
        tmp_path,
        "thread.json",
        [{"post": "p1", "comments": [{"author": "alice", "content": "t1"}, {"author": "bob", "content": "t2"}]}],
    )
    profile = profile_source(path, "json")

    comments = profile.stats("comments")
    assert comments is not None
    assert comments.container == "array"
    assert comments.child_array_count == 1

    author = profile.stats("comments.author")
    assert author is not None
    assert author.element_count == 2
    assert profile.stats("comments.0.author") is None  # 聚合路径不带下标


def test_profile_observes_schema_change_near_eof(tmp_path: Path) -> None:
    records = [{"id": f"r{i:04d}", "body": "x" * 50} for i in range(1000)]
    records[-1]["late"] = "appears-only-at-eof"
    path = _json_file(tmp_path, "big.json", records)

    profile = profile_source(path, "json")

    assert profile.record_count == 1000
    late = profile.stats("late")
    assert late is not None
    assert late.element_count == 1


def test_profile_respects_sample_limit(tmp_path: Path) -> None:
    records = [{"id": f"r{i:04d}", "body": "x"} for i in range(3000)]
    path = _json_file(tmp_path, "huge.json", records)

    profile = profile_source(path, "json")

    assert profile.record_count == 3000
    assert profile.stats("id").element_count <= 1000


def test_profile_is_deterministic(tmp_path: Path) -> None:
    records = [{"id": f"r{i:04d}", "body": "text " * 20, "tag": f"t{i % 5}"} for i in range(1200)]
    path = _json_file(tmp_path, "deterministic.json", records)

    first = profile_source(path, "json")
    second = profile_source(path, "json")

    assert first.record_count == second.record_count == 1200
    assert schema_fingerprint(first) == schema_fingerprint(second)
    assert set(first.paths) == set(second.paths)
    for name in first.paths:
        a, b = first.paths[name], second.paths[name]
        assert a.uniqueness == b.uniqueness
        assert a.avg_length == b.avg_length
        assert a.null_rate == b.null_rate
        assert a.datetime_parse_rate == b.datetime_parse_rate


def test_profile_jsonl(tmp_path: Path) -> None:
    lines = [{"id": f"r{i}", "body": "x" * 10} for i in range(5)]
    path = _write(
        tmp_path, "data.jsonl", ("\n".join(json.dumps(l) for l in lines) + "\n").encode()
    )

    profile = profile_source(path, "jsonl")

    assert profile.record_count == 5
    assert profile.stats("id").uniqueness == 1.0


def test_profile_malformed_source_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "truncated.json", b'[{"id": 1}, {"id": 2')
    with pytest.raises(SourceSyntaxError):
        profile_source(path, "json")


def test_fingerprint_excludes_values(tmp_path: Path) -> None:
    a = [{"id": "one", "body": "text-a", "tags": ["x"]}, {"id": "two", "body": "text-b", "tags": ["y", "z"]}]
    b = [{"id": "alpha", "body": "different-1", "tags": ["p"]}, {"id": "beta", "body": "different-2", "tags": ["q", "r"]}]
    pa = profile_source(_json_file(tmp_path, "a.json", a), "json")
    pb = profile_source(_json_file(tmp_path, "b.json", b), "json")

    assert schema_fingerprint(pa) == schema_fingerprint(pb)


def test_fingerprint_changes_with_structure(tmp_path: Path) -> None:
    base = [{"id": "1", "body": "x"}]
    extra = [{"id": "1", "body": "x", "optional": "y"}]
    pa = profile_source(_json_file(tmp_path, "base.json", base), "json")
    pb = profile_source(_json_file(tmp_path, "extra.json", extra), "json")

    assert schema_fingerprint(pa) != schema_fingerprint(pb)


def test_compatibility_identical_structure(tmp_path: Path) -> None:
    data = [{"id": "1", "body": "x"}]
    pa = profile_source(_json_file(tmp_path, "a.json", data), "json")
    pb = profile_source(_json_file(tmp_path, "b.json", data), "json")

    result = compatibility(pa, pb)

    assert isinstance(result, Compatibility)
    assert result.kind == "identical"


def test_compatibility_adding_optional_field(tmp_path: Path) -> None:
    old = [{"id": "1", "body": "x"}]
    new = [{"id": "1", "body": "x", "optional": "y"}]
    result = compatibility(
        profile_source(_json_file(tmp_path, "old.json", old), "json"),
        profile_source(_json_file(tmp_path, "new.json", new), "json"),
    )

    assert result.kind == "compatible"
    assert "added:optional" in result.differences


def test_compatibility_removing_body_is_breaking(tmp_path: Path) -> None:
    old = [{"id": "1", "body": "x"}]
    new = [{"id": "1"}]
    result = compatibility(
        profile_source(_json_file(tmp_path, "old.json", old), "json"),
        profile_source(_json_file(tmp_path, "new.json", new), "json"),
    )

    assert result.kind == "breaking"
    assert any(d.startswith("removed:body") for d in result.differences)


def test_compatibility_array_to_object_is_breaking(tmp_path: Path) -> None:
    old = [{"comments": [{"a": 1}, {"a": 2}]}]
    new = [{"comments": {"a": 1}}]
    result = compatibility(
        profile_source(_json_file(tmp_path, "old.json", old), "json"),
        profile_source(_json_file(tmp_path, "new.json", new), "json"),
    )

    assert result.kind == "breaking"
