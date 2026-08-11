from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Union

JsonScalar = Union[str, int, float, bool, None]
FilterOp = Literal["eq", "in", "gte", "lte"]

MAX_FILTERS = 20


@dataclass(frozen=True)
class SearchFilter:
    """One explicit metadata predicate over a filterable field.

    ``op`` is one of ``eq``, ``in`` (list membership), ``gte``, ``lte``.
    Range operators only accept number or datetime values; equality and
    membership accept any JSON scalar.
    """

    field: str
    op: FilterOp
    value: JsonScalar | list[JsonScalar]

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not self.field.strip():
            raise ValueError("filter field must be a non-empty string")
        if self.op not in ("eq", "in", "gte", "lte"):
            raise ValueError(f"unsupported filter operator: {self.op!r}")
        if self.op == "in":
            if not isinstance(self.value, list):
                raise ValueError("filter op 'in' requires a list value")
            if not self.value:
                raise ValueError("filter op 'in' requires at least one value")
            for item in self.value:
                if isinstance(item, (dict, list)):
                    raise ValueError("filter values must be JSON scalars, not nested objects")
            value_types = {normalize_filter_value(item)[0] for item in self.value}
            if len(value_types) > 1:
                raise ValueError("filter op 'in' must not mix value types")
        else:
            if isinstance(self.value, list):
                raise ValueError("list values are only allowed with filter op 'in'")
            if isinstance(self.value, dict):
                raise ValueError("filter values must be JSON scalars, not nested objects")


@dataclass(frozen=True)
class SearchQuery:
    """A retrieval query with explicit metadata filters."""

    text: str
    filters: tuple[SearchFilter, ...] = ()

    def __post_init__(self) -> None:
        if len(self.filters) > MAX_FILTERS:
            raise ValueError(
                f"too many filters: {len(self.filters)} > {MAX_FILTERS}"
            )


def normalize_filter_value(value: Any) -> tuple[str, str]:
    """Normalize a filter value into ``(value_type, normalized_value)``.

    ``value_type`` is one of ``string|number|boolean|datetime|null``.
    Strings are NFKC-normalized (not lowercased), booleans stay distinct
    from integers, numbers become canonical decimal strings, and datetimes
    become UTC ISO-8601 strings. Nested objects and non-finite numbers are
    rejected.
    """
    if value is None:
        return ("null", "")
    if isinstance(value, bool):
        return ("boolean", "true" if value else "false")
    if isinstance(value, int):
        return ("number", _canonical_number(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not filterable")
        return ("number", _canonical_number(value))
    if isinstance(value, str):
        return ("string", unicodedata.normalize("NFKC", value))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return ("datetime", value.isoformat(timespec="seconds"))
    raise ValueError(f"unsupported filter value type: {type(value).__name__}")


def _canonical_number(value: int | float) -> str:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        raise ValueError(f"non-finite number is not filterable: {value!r}") from None


def filter_chunk_ids(
    index: Any,
    collection_id: str,
    active_run_ids: tuple[str, ...],
    filters: tuple[SearchFilter, ...],
) -> set[str] | None:
    """Intersect per-filter EAV lookups into one ``chunk_id`` set.

    Returns ``None`` when no filters are given, the (possibly empty) set of
    matching ``chunk_id`` values otherwise. Every value and field name is
    bound as a SQL parameter; no user text is interpolated into SQL.
    """
    if not filters:
        return None
    if len(filters) > MAX_FILTERS:
        raise ValueError(f"too many filters: {len(filters)} > {MAX_FILTERS}")
    if not active_run_ids:
        return set()

    connection = index.connection()
    try:
        index._initialize(connection)
        result: set[str] | None = None
        for search_filter in filters:
            matches = _matching_chunk_ids(
                connection, collection_id, active_run_ids, search_filter
            )
            result = matches if result is None else result & matches
            if not result:
                return set()
        return result
    finally:
        connection.close()


def _matching_chunk_ids(
    connection: Any,
    collection_id: str,
    active_run_ids: tuple[str, ...],
    search_filter: SearchFilter,
) -> set[str]:
    placeholders = ", ".join("?" for _ in active_run_ids)
    sql = (
        "SELECT chunk_id FROM chunk_filter "
        "WHERE collection_id = ? AND index_state = 'active' "
        f"AND ingest_run_id IN ({placeholders}) AND field_name = ?"
    )
    parameters: list[Any] = [collection_id, *active_run_ids, search_filter.field]

    op = search_filter.op
    if op == "eq":
        value_type, normalized = normalize_filter_value(search_filter.value)
        sql += " AND value_type = ? AND normalized_value = ?"
        parameters.extend([value_type, normalized])
    elif op == "in":
        value_type: str | None = None
        normalized_values: list[str] = []
        for item in search_filter.value:
            item_type, normalized = normalize_filter_value(item)
            if value_type is None:
                value_type = item_type
            normalized_values.append(normalized)
        inner = ", ".join("?" for _ in normalized_values)
        sql += f" AND value_type = ? AND normalized_value IN ({inner})"
        parameters.append(value_type)
        parameters.extend(normalized_values)
    elif op in ("gte", "lte"):
        value_type, normalized = normalize_filter_value(search_filter.value)
        if value_type not in ("number", "datetime"):
            raise ValueError(
                f"filter op {op!r} only supports number or datetime values"
            )
        if value_type == "number":
            typed_column = "numeric_value"
            typed_value: str | float = float(normalized)
        else:
            typed_column = "datetime_value"
            typed_value = normalized
        comparison = ">=" if op == "gte" else "<="
        sql += f" AND value_type = ? AND {typed_column} {comparison} ?"
        parameters.extend([value_type, typed_value])
    else:  # pragma: no cover - guarded by SearchFilter validation
        raise ValueError(f"unsupported filter operator: {op!r}")

    rows = connection.execute(sql, parameters).fetchall()
    return {row["chunk_id"] for row in rows}
