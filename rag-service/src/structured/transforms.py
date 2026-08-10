"""字段转换、校验条件与父子记录生成。

- TRANSFORMS 是固定注册表（声明名 → 纯函数），绝不 import 用户输入命名的
  可调用对象。
- 转换按声明顺序作用于字段值；条件在转换之后判定。
- map_record 生成标准记录：父先于子；稳定 ID 取输入 ID（规范化）或
  fallback hash(source_hash, source_pointer, record_type)。
- 关系规则：子记录 source_field 的值匹配最近前序同级 target_field 的值时，
  该前序同级成为父/上下文记录。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Iterator

from src.structured.models import (
    FieldCondition,
    FieldMapping,
    MappedRecord,
    MappingIssue,
    RecordTypeMapping,
    RelationRule,
    SourceRecord,
)
from src.structured.paths import compile_path, resolve_one, resolve_many


# ---------------------------------------------------------------- 转换注册表

def trim(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def normalize_whitespace(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


def normalize_newlines(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return value


def strip_html(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = re.sub(r"<[^>]+>", "", value)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def join(value: Any, separator: str = ", ") -> Any:
    if isinstance(value, (list, tuple)):
        return separator.join(str(item) for item in value)
    return value


def to_string(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def to_number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise ValueError("布尔值不能转为数字")
    if isinstance(value, (int, float)):
        return value
    return float(value)


def to_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "是", "真"):
            return True
        if text in ("false", "0", "no", "n", "否", "假"):
            return False
    raise ValueError(f"无法解析为布尔值: {value!r}")


def parse_datetime(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("空值无法解析为时间")
    text = value.strip().replace("Z", "+00:00")
    # 兼容 "2026-01-01 08:00:00" 空格分隔
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def deduplicate(value: Any) -> Any:
    if isinstance(value, list):
        seen: set[Any] = set()
        out: list[Any] = []
        for item in value:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else item
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out
    if isinstance(value, str):
        return "\n".join(dict.fromkeys(value.splitlines()))
    return value


def remove_child_echo(value: Any, child_texts: list[str] = ()) -> Any:
    """去除父正文中重复拼接的子记录文本（通用父子内容去重）。

    以整行/段落为单位删除：父正文通常把子记录文本拼成独立段落，
    直接逐字替换会破坏周围文本（如 "评论A评论B" 中删除 A 会把 B 拼上）。
    """
    if not isinstance(value, str):
        return value
    lines = value.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(stripped == text.strip() for text in child_texts if text.strip()):
            continue  # 整行与子记录文本一致 → 回显，删除
        kept.append(line)
    return "\n".join(kept)


TRANSFORMS: dict[str, Callable[..., Any]] = {
    "trim": trim,
    "normalize_whitespace": normalize_whitespace,
    "normalize_newlines": normalize_newlines,
    "strip_html": strip_html,
    "join": join,
    "to_string": to_string,
    "to_number": to_number,
    "to_boolean": to_boolean,
    "parse_datetime": parse_datetime,
    "deduplicate": deduplicate,
    "remove_child_echo": remove_child_echo,
}

# 可带额外参数（以关键字传入）的转换
_TRANSFORM_WITH_ARGS = {"join", "remove_child_echo"}


# ---------------------------------------------------------------- 条件

def _apply_conditions(
    value: Any,
    conditions: tuple[FieldCondition, ...],
    issue: Callable[[MappingIssue], None],
    *,
    record_type: str,
    source_pointer: str,
    field_name: str,
) -> tuple[bool, bool]:
    """应用条件，返回 (ok, warn_only)。

    ok=False 且 warn_only=True → 记录级警告，该字段置 None；
    ok=False 且 warn_only=False → 记录级错误，整条记录隔离。
    """
    for condition in conditions:
        kind = condition.kind
        if kind == "required":
            if value is None or (isinstance(value, str) and not value.strip()):
                issue(MappingIssue(
                    kind="record_error", record_type=record_type,
                    source_pointer=source_pointer, code="FIELD_REQUIRED",
                    message=f"必填字段 {field_name} 缺失或为空", field=field_name,
                ))
                return False, False
        elif kind == "min_length":
            if isinstance(value, str) and len(value) < condition.value:
                issue(MappingIssue(
                    kind="warning", record_type=record_type,
                    source_pointer=source_pointer, code="FIELD_TOO_SHORT",
                    message=f"字段 {field_name} 长度不足（{len(value)} < {condition.value}）",
                    field=field_name,
                ))
                return False, True
        elif kind == "max_length":
            if isinstance(value, str) and len(value) > condition.value:
                issue(MappingIssue(
                    kind="warning", record_type=record_type,
                    source_pointer=source_pointer, code="FIELD_TOO_LONG",
                    message=f"字段 {field_name} 超长（{len(value)} > {condition.value}）",
                    field=field_name,
                ))
                return False, True
        elif kind == "not_empty":
            if value is None or (isinstance(value, str) and not value.strip()):
                issue(MappingIssue(
                    kind="warning", record_type=record_type,
                    source_pointer=source_pointer, code="FIELD_EMPTY",
                    message=f"字段 {field_name} 为空", field=field_name,
                ))
                return False, True
        elif kind == "skip_if_only_emoji":
            if isinstance(value, str) and value and not re.sub(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\s]", "", value):
                return False, True
        elif kind == "skip_if_matches":
            if isinstance(value, str) and condition.value and re.search(condition.value, value):
                return False, True
    return True, False


# ---------------------------------------------------------------- 上下文与主流程

@dataclass
class MappingContext:
    """map_record 的共享上下文。on_issue 用于逐记录收集错误/警告。"""

    source_hash: str
    mapping_version_id: int
    on_issue: Callable[[MappingIssue], None] | None = None
    _issue_bucket: list[MappingIssue] = field(default_factory=list)

    def emit(self, issue: MappingIssue) -> None:
        self._issue_bucket.append(issue)
        if self.on_issue is not None:
            self.on_issue(issue)


def _fallback_id(source_hash: str, source_pointer: str, record_type: str) -> str:
    payload = f"{source_hash}\0{source_pointer}\0{record_type}".encode()
    return hashlib.sha256(payload).hexdigest()


def _resolve(value: Any, path: str) -> Any:
    compiled = compile_path(path, relative=True)
    return resolve_one(value, compiled)


# 字段提取结果：ok / 字段级警告丢弃 / 记录级错误（隔离整条记录）
_OK, _WARN_DROP, _RECORD_ERROR = "ok", "warn_drop", "record_error"


def _extract_field(
    record: dict[str, Any],
    field: FieldMapping,
    issue: Callable[[MappingIssue], None],
    *,
    record_type: str,
    source_pointer: str,
    child_texts: tuple[str, ...] = (),
) -> tuple[Any, str]:
    """提取 + 转换 + 条件。返回 (value, status)。"""
    try:
        raw = _resolve(record, field.path)
    except (KeyError, IndexError, TypeError):
        raw = None

    value = raw
    try:
        for name in field.transforms:
            if name not in TRANSFORMS:
                issue(MappingIssue(
                    kind="warning", record_type=record_type,
                    source_pointer=source_pointer, code="UNKNOWN_TRANSFORM",
                    message=f"未知转换 {name!r}（字段 {field.path}，该字段被丢弃）", field=field.path,
                ))
                return None, _WARN_DROP
            fn = TRANSFORMS[name]
            if name == "remove_child_echo":
                value = remove_child_echo(value, list(child_texts))
            else:
                value = fn(value)
    except (ValueError, TypeError) as exc:
        issue(MappingIssue(
            kind="record_error", record_type=record_type,
            source_pointer=source_pointer, code="TRANSFORM_FAILED",
            message=f"字段 {field.path} 转换失败: {exc}", field=field.path,
        ))
        return None, _RECORD_ERROR

    ok, warn_only = _apply_conditions(
        value, field.conditions, issue,
        record_type=record_type, source_pointer=source_pointer, field_name=field.path,
    )
    if not ok:
        return None, (_WARN_DROP if warn_only else _RECORD_ERROR)
    return value, _OK


def _emit_record(
    ctx: MappingContext,
    *,
    record_type: str,
    record: dict[str, Any],
    source_pointer: str,
    parent_id: str | None,
    mapping: RecordTypeMapping,
    child_texts: tuple[str, ...] = (),
) -> MappedRecord | None:
    """构建一条标准记录；返回 None 表示记录被隔离（记录级错误）。"""
    issue = lambda i: ctx.emit(i)

    record_id: str | None = None
    title: str | None = None
    content_parts: list[str] = []
    keywords: list[str] = []
    filters: dict[str, Any] = {}
    timestamps: dict[str, str] = {}
    display: dict[str, Any] = {}

    for field in mapping.fields:
        if field.role == "ignore":
            continue
        value, status = _extract_field(
            record, field, issue,
            record_type=record_type, source_pointer=source_pointer,
            child_texts=child_texts,
        )
        if status == _RECORD_ERROR:
            return None  # 隔离整条记录
        if status == _WARN_DROP:
            continue
        name = field.name or field.path.rsplit(".", 1)[-1].split("[", 1)[0]
        if field.role == "id":
            if isinstance(value, (str, int)):
                record_id = str(value).strip()
        elif field.role == "title":
            if isinstance(value, str):
                title = value
        elif field.role == "content":
            if isinstance(value, str) and value.strip():
                content_parts.append(value)
        elif field.role == "keyword":
            if isinstance(value, str) and value.strip():
                keywords.append(value)
            elif isinstance(value, list):
                keywords.extend(str(v) for v in value if str(v).strip())
        elif field.role == "filter":
            if isinstance(value, (str, int, float, bool)) or value is None:
                filters[name] = value
            elif isinstance(value, list) and all(
                isinstance(v, (str, int, float, bool)) or v is None for v in value
            ):
                filters[name] = value
            else:
                issue(MappingIssue(
                    kind="warning", record_type=record_type,
                    source_pointer=source_pointer, code="FILTER_TYPE_MISMATCH",
                    message=f"过滤字段 {field.path} 不是 JSON 标量/标量列表，已丢弃",
                    field=field.path,
                ))
        elif field.role == "timestamp":
            if isinstance(value, str) and value.strip():
                try:
                    timestamps[name] = parse_datetime(value)
                except ValueError:
                    issue(MappingIssue(
                        kind="warning", record_type=record_type,
                        source_pointer=source_pointer, code="DATETIME_PARSE_FAILED",
                        message=f"时间字段 {field.path} 解析失败，保留展示值",
                        field=field.path,
                    ))
                    display[name] = value
        elif field.role == "display":
            display[name] = value

    if not record_id:
        record_id = _fallback_id(ctx.source_hash, source_pointer, record_type)

    return MappedRecord(
        record_id=record_id,
        parent_id=parent_id,
        record_type=record_type,
        title=title,
        content="\n".join(content_parts) if content_parts else None,
        keywords=tuple(keywords),
        filters=filters,
        timestamps=timestamps,
        display=display,
        raw=dict(record),
        source_pointer=source_pointer,
        mapping_version_id=ctx.mapping_version_id,
    )


def _apply_relations(
    siblings: list[dict[str, Any]],
    relations: tuple[RelationRule, ...],
) -> list[int | None]:
    """对同级记录应用关系规则：返回每条记录最近前序匹配同级的下标（或 None）。

    source_field 的值匹配最近前序同级 target_field 的值 → 该前序同级成为父记录。
    """
    parents: list[int | None] = [None] * len(siblings)
    for relation in relations:
        last_target: int | None = None
        for i, sibling in enumerate(siblings):
            source_value = sibling.get(relation.source_field)
            if last_target is not None and source_value is not None and str(source_value) == str(
                siblings[last_target].get(relation.target_field)
            ):
                parents[i] = last_target
            if sibling.get(relation.target_field) is not None:
                last_target = i
    return parents


def map_record(
    source: SourceRecord,
    mapping: RecordTypeMapping,
    context: MappingContext,
) -> Iterator[MappedRecord]:
    """把一条源记录按映射生成标准记录流：父先于子，子记录含 parent_id。

    父记录的 remove_child_echo 需要子记录文本，因此先提取子记录再产出父记录。
    """
    record_value = source.value if isinstance(source.value, dict) else {"value": source.value}
    child_batches: list[tuple[RecordTypeMapping, list[Any], list[MappedRecord]]] = []
    child_texts: list[str] = []

    # 1) 先提取全部子记录（收集文本供父记录去回显）
    for child_mapping in mapping.children:
        try:
            items = resolve_many(
                record_value, compile_path(child_mapping.record_path, relative=True)
            )
        except (KeyError, IndexError, TypeError):
            continue
        batch_items: list[Any] = []
        batch_records: list[MappedRecord] = []
        array_name = child_mapping.record_path.split("[", 1)[0]
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            child_pointer = f"{source.source_pointer}/{array_name}/{index}"
            mapped = _emit_record(
                context,
                record_type=child_mapping.name,
                record=item,
                source_pointer=child_pointer,
                parent_id=None,  # 父关系在产出时统一赋值
                mapping=child_mapping,
            )
            if mapped is None:
                continue
            batch_items.append(item)
            batch_records.append(mapped)
            if mapped.content:
                child_texts.append(mapped.content)
        child_batches.append((child_mapping, batch_items, batch_records))

    # 2) 父记录（用全部子文本做 remove_child_echo）
    parent = _emit_record(
        context,
        record_type=mapping.name,
        record=record_value,
        source_pointer=source.source_pointer,
        parent_id=None,
        mapping=mapping,
        child_texts=tuple(child_texts),
    )
    if parent is None:
        return
    yield parent

    # 3) 子记录：默认父为 post；关系规则用最近前序同级覆盖
    for child_mapping, batch_items, batch_records in child_batches:
        relations: list[int | None] = [None] * len(batch_records)
        if child_mapping.relations:
            relations = _apply_relations(batch_items, child_mapping.relations)
        for i, mapped in enumerate(batch_records):
            if relations[i] is not None:
                mapped = mapped.model_copy(
                    update={"parent_id": batch_records[relations[i]].record_id}
                )
            else:
                mapped = mapped.model_copy(update={"parent_id": parent.record_id})
            yield mapped
