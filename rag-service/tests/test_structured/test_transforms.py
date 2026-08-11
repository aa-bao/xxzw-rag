"""转换、条件、父子记录与稳定 ID 测试。"""
from __future__ import annotations

import hashlib

import pytest

from src.structured.models import (
    FieldCondition,
    FieldMapping,
    MappingIssue,
    RelationRule,
    RecordTypeMapping,
    SourceRecord,
)
from src.structured.transforms import (
    TRANSFORMS,
    MappingContext,
    deduplicate,
    join,
    map_record,
    normalize_newlines,
    normalize_whitespace,
    parse_datetime,
    remove_child_echo,
    strip_html,
    to_boolean,
    to_number,
    to_string,
    trim,
)


def _context(**overrides) -> MappingContext:
    values = {"source_hash": "src-hash-1", "mapping_version_id": 5}
    values.update(overrides)
    return MappingContext(**values)


def _post_mapping() -> RecordTypeMapping:
    return RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="id", role="id"),
            FieldMapping(path="title", role="title"),
            FieldMapping(path="body", role="content", transforms=("remove_child_echo",)),
        ),
        children=(
            RecordTypeMapping(
                name="comment",
                record_path="comments[*]",
                fields=(
                    FieldMapping(path="author", role="filter", name="author"),
                    FieldMapping(path="content", role="content", name="content"),
                ),
                relations=(RelationRule(source_field="referTo", target_field="author"),),
            ),
        ),
    )


def test_transform_registry_has_exact_names() -> None:
    assert set(TRANSFORMS) == {
        "trim",
        "normalize_whitespace",
        "normalize_newlines",
        "strip_html",
        "join",
        "to_string",
        "to_number",
        "to_boolean",
        "parse_datetime",
        "deduplicate",
        "remove_child_echo",
    }
    for fn in TRANSFORMS.values():
        assert callable(fn)


def test_trim() -> None:
    assert trim("  hello  ") == "hello"
    assert trim(" \t x \n ") == "x"


def test_normalize_whitespace() -> None:
    assert normalize_whitespace("a   b\t\tc") == "a b c"
    assert normalize_whitespace("  a b  ") == "a b"


def test_normalize_newlines() -> None:
    assert normalize_newlines("a\r\nb\r\nc") == "a\nb\nc"


def test_strip_html() -> None:
    assert strip_html("<p>hi <b>there</b></p>") == "hi there"
    assert strip_html("a &amp; b") == "a & b"


def test_join() -> None:
    assert join(["a", "b", "c"]) == "a, b, c"
    assert join([1, 2]) == "1, 2"
    assert join("not-a-list") == "not-a-list"


def test_to_string() -> None:
    assert to_string(42) == "42"
    assert to_string(3.5) == "3.5"
    assert to_string(True) == "True"
    assert to_string(["a", "b"]) == '["a", "b"]'


def test_to_number() -> None:
    assert to_number("42") == 42
    assert to_number("3.5") == 3.5
    assert to_number(7) == 7
    with pytest.raises(ValueError):
        to_number("abc")


def test_to_boolean() -> None:
    assert to_boolean("true") is True
    assert to_boolean("FALSE") is False
    assert to_boolean("1") is True
    assert to_boolean("0") is False
    assert to_boolean("yes") is True
    assert to_boolean("no") is False
    with pytest.raises(ValueError):
        to_boolean("maybe")


def test_parse_datetime() -> None:
    assert parse_datetime("2026-01-01T08:00:00Z") == "2026-01-01T08:00:00+00:00"
    assert parse_datetime("2026-01-01 08:00:00") == "2026-01-01T08:00:00+00:00"
    with pytest.raises(ValueError):
        parse_datetime("not a date")


def test_deduplicate() -> None:
    assert deduplicate(["a", "b", "a", "c"]) == ["a", "b", "c"]
    assert deduplicate("l1\nl2\nl1") == "l1\nl2"


def test_remove_child_echo() -> None:
    result = remove_child_echo("正文内容\n评论A\n评论B", ["评论A"])
    assert result == "正文内容\n评论B"
    assert result.count("评论A") == 0


def test_map_record_parent_before_children_with_ids() -> None:
    source = SourceRecord(
        value={
            "id": "p1",
            "title": "标题",
            "body": "正文",
            "comments": [
                {"author": "A", "content": "c1"},
                {"author": "B", "content": "c2"},
            ],
        },
        source_pointer="$",
    )
    records = list(map_record(source, _post_mapping(), _context()))

    assert [r.record_type for r in records] == ["post", "comment", "comment"]
    assert records[0].record_id == "p1"
    assert records[0].parent_id is None
    assert records[1].parent_id == records[0].record_id
    assert records[2].parent_id == records[0].record_id
    assert records[0].raw == source.value
    assert records[1].raw == {"author": "A", "content": "c1"}
    assert records[1].source_pointer == "$/comments/0"
    assert records[2].source_pointer == "$/comments/1"
    assert records[1].filters == {"author": "A"}


def test_map_record_fallback_id_from_hash_pointer_type() -> None:
    source = SourceRecord(value={"title": "t", "body": "b"}, source_pointer="/0")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(path="body", role="content"),
        ),
    )
    records = list(map_record(source, mapping, _context(source_hash="abc123")))

    expected = hashlib.sha256(b"abc123\0/0\0post").hexdigest()
    assert records[0].record_id == expected


def test_map_record_child_fallback_id() -> None:
    source = SourceRecord(
        value={
            "id": "p1",
            "body": "正文",
            "comments": [{"author": "A", "content": "c1"}, {"author": "A", "content": "c2"}],
        },
        source_pointer="/posts/0",
    )
    records = list(map_record(source, _post_mapping(), _context(source_hash="abc123")))

    expected = hashlib.sha256(b"abc123\0/posts/0/comments/1\0comment").hexdigest()
    assert records[2].record_id == expected


def test_relation_links_nearest_prior_sibling() -> None:
    """brief 测试：comment 2 referTo='A' → 最近前序作者为 A 的同级成为父记录。"""
    comments = [
        {"author": "A", "content": "评论A内容"},
        {"referTo": "A", "content": "回复A的内容"},
        {"author": "B", "content": "独立评论"},
    ]
    source = SourceRecord(
        value={
            "id": "p1",
            "title": "标题",
            "body": "正文内容\n" + comments[0]["content"] + "\n" + comments[2]["content"],
            "comments": comments,
        },
        source_pointer="$",
    )
    records = list(map_record(source, _post_mapping(), _context()))

    assert records[0].parent_id is None
    assert records[1].parent_id == records[0].record_id
    assert records[2].parent_id == records[1].record_id
    # remove_child_echo：父正文不含子记录内容
    assert records[0].content.count(records[1].content) == 0


def test_required_missing_field_isolates_record() -> None:
    source = SourceRecord(value={"title": "t"}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(
                path="body",
                role="content",
                conditions=(FieldCondition(kind="required"),),
            ),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert records == []
    assert len(issues) == 1
    assert issues[0].kind == "record_error"
    assert issues[0].code == "FIELD_REQUIRED"
    assert issues[0].record_type == "post"
    assert issues[0].field == "body"


def test_min_length_warns_and_drops_field() -> None:
    source = SourceRecord(value={"title": "t", "body": "短"}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(
                path="body",
                role="content",
                conditions=(FieldCondition(kind="min_length", value=10),),
            ),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert len(records) == 1
    assert records[0].content is None
    assert issues[0].kind == "warning"
    assert issues[0].code == "FIELD_TOO_SHORT"


def test_not_empty_warns_on_empty_value() -> None:
    source = SourceRecord(value={"title": "t", "body": ""}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(
                path="body",
                role="content",
                conditions=(FieldCondition(kind="not_empty"),),
            ),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert records[0].content is None
    assert issues[0].kind == "warning"
    assert issues[0].code == "FIELD_EMPTY"


def test_skip_if_only_emoji_silently_skips() -> None:
    source = SourceRecord(value={"title": "t", "body": "👍👍"}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(
                path="body",
                role="content",
                conditions=(FieldCondition(kind="skip_if_only_emoji"),),
            ),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert records[0].content is None
    assert issues == []


def test_skip_if_matches_silently_skips() -> None:
    source = SourceRecord(value={"title": "t", "body": "2026-01-01"}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(
                path="body",
                role="content",
                conditions=(FieldCondition(kind="skip_if_matches", value=r"^\d{4}-\d{2}-\d{2}$"),),
            ),
        ),
    )
    records = list(map_record(source, mapping, _context()))

    assert records[0].content is None


def test_content_fields_joined_in_declared_order() -> None:
    source = SourceRecord(value={"title": "t", "intro": "开头", "body": "正文"}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(path="intro", role="content"),
            FieldMapping(path="body", role="content"),
        ),
    )
    records = list(map_record(source, mapping, _context()))

    assert records[0].content == "开头\n正文"


def test_filters_normalized_to_json_scalars() -> None:
    source = SourceRecord(
        value={"title": "t", "category": "x", "tags": ["a", "b"], "bad": {"nested": 1}},
        source_pointer="$",
    )
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(path="category", role="filter", name="category"),
            FieldMapping(path="tags", role="filter", name="tags"),
            FieldMapping(path="bad", role="filter", name="bad"),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert records[0].filters == {"category": "x", "tags": ["a", "b"]}
    assert issues[0].kind == "warning"
    assert issues[0].code == "FILTER_TYPE_MISMATCH"


def test_timestamp_parsed_and_failure_kept_in_display() -> None:
    source = SourceRecord(
        value={"title": "t", "date": "2026-01-01T08:00:00Z", "bad_date": "昨天"},
        source_pointer="$",
    )
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(path="date", role="timestamp", name="created_at"),
            FieldMapping(path="bad_date", role="timestamp", name="bad_date"),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert records[0].timestamps == {"created_at": "2026-01-01T08:00:00+00:00"}
    assert issues[0].kind == "warning"
    assert issues[0].code == "DATETIME_PARSE_FAILED"
    # 解析失败：保留原始展示值，不写入时间过滤字段
    assert "bad_date" not in records[0].timestamps
    assert records[0].display["bad_date"] == "昨天"


def test_unknown_transform_skips_field_with_warning() -> None:
    source = SourceRecord(value={"title": "t", "body": "x"}, source_pointer="$")
    mapping = RecordTypeMapping(
        name="post",
        record_path="$",
        fields=(
            FieldMapping(path="title", role="title"),
            FieldMapping(path="body", role="content", transforms=("exec",)),
        ),
    )
    issues: list[MappingIssue] = []
    records = list(map_record(source, mapping, _context(on_issue=issues.append)))

    assert len(records) == 1
    assert records[0].content is None
    assert issues[0].kind == "warning"
    assert issues[0].code == "UNKNOWN_TRANSFORM"
