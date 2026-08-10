"""流式记录源：ijson 增量解析 JSON / 逐行 JSONL。

错误语义（设计文档 6.2）：
- JSON/JSONL 语法错误、截断 → 整个文档失败，抛 SourceSyntaxError
  （消息含源指针或字节位置）。
- JSONL 根数组/根对象分别按行元素与整体产出。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Literal

import ijson

from src.structured.models import SourceRecord
from src.structured.paths import CompiledPath, PathSyntaxError


class SourceSyntaxError(ValueError):
    """源文件语法错误或截断，消息携带源指针/字节位置。"""


def _as_source_error(exc: Exception, source_pointer: str) -> SourceSyntaxError:
    pointer = getattr(exc, "pointer", None)
    pos = getattr(exc, "pos", None)
    line = getattr(exc, "line", None)
    col = getattr(exc, "col", None)
    location = source_pointer
    if pointer:
        location = f"{location} @ {pointer}"
    elif pos is not None:
        location = f"{location} @ byte {pos}"
    elif line is not None and col is not None:
        location = f"{location} @ line {line} col {col}"
    message = str(exc) or exc.__class__.__name__
    return SourceSyntaxError(f"{location}: {message}")


def iter_source(
    path: Path,
    source_format: Literal["json", "jsonl"],
    record_path: CompiledPath,
) -> Iterator[SourceRecord]:
    """流式迭代源记录。

    JSON：record_path 决定记录位置；根对象（$）整体为一条记录，
    根数组（$[*]）逐元素产出（pointer /0..），子数组（$.posts[*]）
    经 ijson 前缀转换后逐元素产出（pointer /posts/0..）。
    JSONL：逐行解析（ijson multiple_values），每行一条记录
    （pointer /line/N，从 1 开始）；每行必须是一个完整 JSON 值。
    """
    with path.open("rb") as handle:
        if source_format == "jsonl":
            yield from _iter_jsonl(handle)
            return

        if record_path.wildcard_index is not None:
            yield from _iter_json_array(handle, record_path)
            return

        # 根对象 / 标量：整份文档作为一条记录
        prefix = record_path.ijson_prefix()
        try:
            for value in ijson.items(handle, prefix, multiple_values=True):
                yield SourceRecord(value=value, source_pointer="$")
        except ijson.JSONError as exc:
            raise _as_source_error(exc, "$") from exc
        except PathSyntaxError as exc:
            raise SourceSyntaxError(str(exc)) from exc


def _iter_json_array(handle: Any, record_path: CompiledPath) -> Iterator[SourceRecord]:
    prefix = record_path.ijson_prefix()
    base_pointer = _pointer_base(record_path)
    try:
        for index, value in enumerate(
            ijson.items(handle, prefix, multiple_values=True), start=0
        ):
            yield SourceRecord(value=value, source_pointer=f"{base_pointer}{index}")
    except ijson.JSONError as exc:
        pointer = f"{base_pointer}~"
        raise _as_source_error(exc, pointer) from exc
    except PathSyntaxError as exc:
        raise SourceSyntaxError(str(exc)) from exc


def _iter_jsonl(handle: Any) -> Iterator[SourceRecord]:
    head = handle.read(1)
    handle.seek(0)
    if not head:
        return  # 空文件：零条记录
    try:
        for line_number, value in enumerate(
            ijson.items(handle, "", multiple_values=True), start=1
        ):
            yield SourceRecord(value=value, source_pointer=f"/line/{line_number}")
    except ijson.JSONError as exc:
        raise _as_source_error(exc, "/line/") from exc
    except PathSyntaxError as exc:
        raise SourceSyntaxError(str(exc)) from exc


def _pointer_base(record_path: CompiledPath) -> str:
    parts: list[str] = []
    for token in record_path.tokens[: record_path.wildcard_index]:
        if token.kind == "field":
            parts.append(str(token.value))
        elif token.kind == "index":
            parts.append(str(token.value))
    return "/" + "/".join(parts) + "/" if parts else "/"
