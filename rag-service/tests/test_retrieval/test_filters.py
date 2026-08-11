from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.retrieval.filters import (
    MAX_FILTERS,
    SearchFilter,
    SearchQuery,
    filter_chunk_ids,
    normalize_filter_value,
)
from src.retrieval.fts import FtsIndex, StagedChunk


def _chunk(
    chunk_id: str,
    metadata: dict,
    run_id: str = "run1",
    collection_id: str = "col1",
) -> StagedChunk:
    return StagedChunk(
        chunk_id=chunk_id,
        title="",
        keywords="",
        content="免税",
        collection_id=collection_id,
        ingest_run_id=run_id,
        metadata=metadata,
    )


def _index(tmp_path: Path, chunks: list[StagedChunk]) -> FtsIndex:
    index = FtsIndex(tmp_path / "retrieval.db")
    index.initialize()
    index.stage_chunks(chunks)
    index.activate_run("col1", "run1")
    return index


def test_reject_empty_field_name() -> None:
    with pytest.raises(ValueError, match="field"):
        SearchFilter("", "eq", "x")


def test_reject_nested_object_value() -> None:
    with pytest.raises(ValueError, match="nested"):
        SearchFilter("meta", "eq", {"a": 1})
    with pytest.raises(ValueError, match="nested"):
        SearchFilter("meta", "in", [1, {"a": 1}])


def test_reject_list_for_non_in_op() -> None:
    with pytest.raises(ValueError, match="list"):
        SearchFilter("category", "eq", ["a", "b"])


def test_reject_more_than_twenty_filters() -> None:
    filters = tuple(
        SearchFilter(f"field_{index}", "eq", index) for index in range(MAX_FILTERS + 1)
    )
    with pytest.raises(ValueError, match="too many filters"):
        SearchQuery("query", filters)


def test_strings_normalize_with_nfkc() -> None:
    value_type, normalized = normalize_filter_value("ＡＢＣ")
    assert value_type == "string"
    assert normalized == "ABC"


def test_booleans_stay_distinct_from_integers() -> None:
    assert normalize_filter_value(True) == ("boolean", "true")
    assert normalize_filter_value(False) == ("boolean", "false")
    assert normalize_filter_value(1) == ("number", "1")
    assert normalize_filter_value(0) == ("number", "0")
    assert normalize_filter_value(True) != normalize_filter_value(1)


def test_datetimes_normalize_to_utc_iso8601() -> None:
    aware = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)
    naive = datetime(2026, 1, 15, 8, 30)
    assert normalize_filter_value(aware)[0] == "datetime"
    assert normalize_filter_value(aware)[1].endswith("+00:00")
    assert normalize_filter_value(naive)[1].endswith("+00:00")


def test_numbers_have_canonical_decimal_strings() -> None:
    assert normalize_filter_value(3.14) == ("number", "3.14")
    assert normalize_filter_value(2.0) == ("number", "2")
    assert normalize_filter_value(42) == ("number", "42")
    with pytest.raises(ValueError):
        normalize_filter_value(float("nan"))


def test_null_type() -> None:
    assert normalize_filter_value(None) == ("null", "")


def test_filters_and_across_fields(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _chunk("c1", {"category": "policy", "author": "alice"}),
            _chunk("c2", {"category": "policy", "author": "bob"}),
            _chunk("c3", {"category": "guide", "author": "alice"}),
        ],
    )
    result = filter_chunk_ids(
        index,
        "col1",
        ("run1",),
        (
            SearchFilter("category", "eq", "policy"),
            SearchFilter("author", "eq", "alice"),
        ),
    )
    assert result == {"c1"}


def test_in_op_is_or_within_one_field(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _chunk("c1", {"category": "policy", "author": "alice"}),
            _chunk("c2", {"category": "policy", "author": "bob"}),
            _chunk("c3", {"category": "guide", "author": "alice"}),
        ],
    )
    result = filter_chunk_ids(
        index,
        "col1",
        ("run1",),
        (SearchFilter("category", "in", ["policy", "guide"]),),
    )
    assert result == {"c1", "c2", "c3"}


def test_in_op_rejects_mixed_value_types() -> None:
    with pytest.raises(ValueError, match="mix"):
        SearchFilter("category", "in", ["a", 1])


def test_number_range_filter(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _chunk("c1", {"year": 2024}),
            _chunk("c2", {"year": 2025}),
            _chunk("c3", {"year": 2026}),
        ],
    )
    result = filter_chunk_ids(
        index,
        "col1",
        ("run1",),
        (SearchFilter("year", "gte", 2025),),
    )
    assert result == {"c2", "c3"}


def test_datetime_range_filter(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _chunk("c1", {"created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}),
            _chunk("c2", {"created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)}),
        ],
    )
    result = filter_chunk_ids(
        index,
        "col1",
        ("run1",),
        (SearchFilter("created_at", "gte", datetime(2026, 3, 1, tzinfo=timezone.utc)),),
    )
    assert result == {"c2"}


def test_range_rejected_for_string(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="number or datetime"):
        filter_chunk_ids(
            _index(tmp_path, [_chunk("c1", {"category": "policy"})]),
            "col1",
            ("run1",),
            (SearchFilter("category", "gte", "policy"),),
        )


def test_no_filters_returns_none(tmp_path: Path) -> None:
    index = _index(tmp_path, [_chunk("c1", {"category": "policy"})])
    assert filter_chunk_ids(index, "col1", ("run1",), ()) is None


def test_no_matches_returns_empty_set(tmp_path: Path) -> None:
    index = _index(tmp_path, [_chunk("c1", {"category": "policy"})])
    result = filter_chunk_ids(
        index,
        "col1",
        ("run1",),
        (SearchFilter("category", "eq", "missing"),),
    )
    assert result == set()


def test_sql_injection_attempts_do_not_leak(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _chunk("c1", {"category": "policy"}),
            _chunk("c2", {"category": "guide"}),
        ],
    )
    for attempt in (
        "policy OR 1=1 --",
        "x; DROP TABLE chunk_meta; --",
        "unused",
    ):
        result = filter_chunk_ids(
            index,
            "col1",
            ("run1",),
            (SearchFilter("category", "eq", attempt),),
        )
        assert result == set()
    assert filter_chunk_ids(index, "col1", ("run1",), (SearchFilter("category", "eq", "policy"),)) == {"c1"}


def test_search_short_circuits_on_empty_filter_set(tmp_path: Path) -> None:
    index = _index(tmp_path, [_chunk("c1", {"category": "policy"})])
    hits = index.search(
        "免税",
        "col1",
        limit=10,
        active_run_ids=("run1",),
        filter_chunk_ids=set(),
    )
    assert hits == []


def test_search_with_filtered_candidates(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _chunk("c1", {"category": "policy"}),
            _chunk("c2", {"category": "guide"}),
        ],
    )
    filtered = filter_chunk_ids(
        index,
        "col1",
        ("run1",),
        (SearchFilter("category", "eq", "guide"),),
    )
    hits = index.search(
        "免税",
        "col1",
        limit=10,
        active_run_ids=("run1",),
        filter_chunk_ids=filtered,
    )
    assert [hit.chunk_id for hit in hits] == ["c2"]


def test_index_filter_chunk_ids_delegates(tmp_path: Path) -> None:
    index = _index(tmp_path, [_chunk("c1", {"category": "policy"})])
    result = index.filter_chunk_ids(
        "col1", ("run1",), (SearchFilter("category", "eq", "policy"),)
    )
    assert result == {"c1"}
    assert index.filter_chunk_ids("col1", ("run1",), ()) is None
