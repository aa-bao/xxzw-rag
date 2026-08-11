"""结构探查器：确定性、有界、不泄漏值。

- 采样策略：前 800 条完整探查 + 后续 200 条确定性采样，总采样上限 1000 条
  （超 1000 条的文件只探查前 1000 条，保证上限与可复现性）。
- 每条路径记录：容器/标量形态、类型集合、null 率、唯一性估计、
  字符串长度 min/max/avg、datetime 解析率、子数组频率，以及 ≤5 条
  脱敏样例（每条截断到 200 字符）。
- 指纹与统计均不含实际值；schema_fingerprint 只依据路径、容器形态与类型。
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from src.structured.paths import PathSyntaxError, compile_path
from src.structured.stream import SourceSyntaxError, iter_source

ContainerKind = Literal["object", "array", "scalar"]

_HEAD_RECORDS = 800
_TAIL_RECORDS = 200
_MAX_SAMPLED_RECORDS = _HEAD_RECORDS + _TAIL_RECORDS
_SAMPLE_EXAMPLES = 5
_MAX_EXAMPLE_CHARS = 200


@dataclass
class PathStats:
    """单一路径的统计（不含值本身；样例为截断后的脱敏值）。"""

    container: ContainerKind | None = None
    types: set[str] = field(default_factory=set)
    observed_count: int = 0
    null_count: int = 0
    element_count: int = 0
    element_distinct: set[str] = field(default_factory=set)
    length_sum: int = 0
    length_min: int | None = None
    length_max: int | None = None
    datetime_parsed: int = 0
    datetime_attempted: int = 0
    child_array_count: int = 0  # 作为子记录数组（元素为对象/数组）的次数
    child_of: str | None = None  # 归属于哪个子记录数组（如 "comments"）
    examples: list[str] = field(default_factory=list)
    is_leaf: bool = True

    @property
    def null_rate(self) -> float:
        if self.observed_count == 0:
            return 0.0
        return self.null_count / self.observed_count

    @property
    def avg_length(self) -> float:
        if self.element_count == 0:
            return 0.0
        return self.length_sum / self.element_count

    @property
    def uniqueness(self) -> float:
        if self.element_count == 0:
            return 0.0
        return len(self.element_distinct) / self.element_count

    @property
    def datetime_parse_rate(self) -> float:
        if self.datetime_attempted == 0:
            return 0.0
        return self.datetime_parsed / self.datetime_attempted


@dataclass
class SourceProfile:
    """探查结果：每路径统计 + 脱敏样例 + 采样记录数。"""

    record_count: int
    paths: dict[str, PathStats]
    source_format: Literal["json", "jsonl"]
    record_path: str
    samples_redacted: bool = True

    def stats(self, path: str) -> PathStats | None:
        return self.paths.get(path)


def _redact(value: Any, max_chars: int = _MAX_EXAMPLE_CHARS) -> str:
    """脱敏：标量截断为有限长度；容器折叠为类型占位（不含内容）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)[:max_chars]
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, (list, tuple)):
        return f"<list:{len(value)}>"
    if isinstance(value, dict):
        return f"<object:{len(value)}>"
    return type(value).__name__


def _try_datetime(value: str) -> bool:
    """宽松日期时间解析探测：ISO 8601（含 Z 后缀）。"""
    text = value.strip()
    if not text:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _canonical_element(value: Any) -> str:
    """值的规范化表示（用于唯一性统计，长字符串走哈希避免驻留原文）。"""
    if value is None:
        return "\0null"
    if isinstance(value, bool):
        return "\0bool:" + ("1" if value else "0")
    if isinstance(value, (int, float)):
        return "\0num:" + repr(value)
    if isinstance(value, str):
        if len(value) > 32:
            return "\0str:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
        return "\0str:" + value
    return "\0obj:" + repr(value)


def _iter_leaves(value: Any, prefix: str, visitor: Any, child_of: str | None = None) -> None:
    """深度优先遍历叶节点，输出 (聚合路径, kind, payload, child_of)。

    - 标量 → (prefix, "scalar", value)
    - 空容器 → (prefix, "object"/"array", None/[])
    - 标量数组 → (prefix, "array", elements)
    - 子记录数组（元素含对象/数组）→ 在 prefix 下记录 "array"（元素统计），
      再以同一 prefix 递归每个元素，且 child_of=该数组名 —— 子记录字段
      路径相对子记录：comments[*] 记录的字段为 "author"、"content"（不带
      "comments." 前缀）。
    """
    if isinstance(value, dict):
        if not value:
            visitor(prefix, "object", None, child_of)
            return
        for key, child in value.items():
            pointer = f"{prefix}.{key}" if prefix else key
            _iter_leaves(child, pointer, visitor, child_of)
    elif isinstance(value, list):
        if not value:
            visitor(prefix, "array", [], child_of)
            return
        has_container = any(isinstance(item, (dict, list)) for item in value)
        if has_container:
            array_name = prefix.rsplit(".", 1)[-1] if prefix else ""
            visitor(prefix, "array", value, child_of)
            for child in value:
                _iter_leaves(child, prefix, visitor, array_name)
        else:
            visitor(prefix, "array", value, child_of)
    else:
        visitor(prefix, "scalar", value, child_of)


class _Profiler:
    def __init__(self) -> None:
        self.paths: dict[str, PathStats] = defaultdict(PathStats)
        self.record_count = 0

    def observe_record(self, value: Any) -> None:
        self.record_count += 1

        def visit(path: str, kind: str, payload: Any, child_of: str | None) -> None:
            stats = self.paths[path]
            if stats.container is None:
                stats.container = kind  # type: ignore[assignment]
            if child_of is not None:
                stats.child_of = child_of
            stats.types.add(kind)
            stats.observed_count += 1
            if kind == "scalar":
                if payload is None:
                    stats.null_count += 1
                else:
                    self._record_element(stats, payload)
            elif kind == "array":
                elements = payload if payload is not None else []
                if any(isinstance(e, (dict, list)) for e in elements):
                    stats.element_count += len(elements)
                    stats.child_array_count += 1
                else:
                    for e in elements:
                        self._record_element(stats, e)
                if not elements and len(stats.examples) < _SAMPLE_EXAMPLES:
                    stats.examples.append("<empty-array>")
            elif kind == "object":
                if len(stats.examples) < _SAMPLE_EXAMPLES:
                    stats.examples.append("<empty-object>")

        _iter_leaves(value, "", visit)

    def _record_element(self, stats: PathStats, value: Any) -> None:
        stats.element_count += 1
        if value is None:
            stats.null_count += 1
            return
        stats.element_distinct.add(_canonical_element(value))
        if isinstance(value, str):
            stats.length_sum += len(value)
            stats.length_min = (
                len(value) if stats.length_min is None else min(stats.length_min, len(value))
            )
            stats.length_max = (
                len(value) if stats.length_max is None else max(stats.length_max, len(value))
            )
            stats.datetime_attempted += 1
            if _try_datetime(value):
                stats.datetime_parsed += 1
            if len(stats.examples) < _SAMPLE_EXAMPLES and value.strip():
                stats.examples.append(_redact(value))
        elif isinstance(value, (int, float, bool)):
            if len(stats.examples) < _SAMPLE_EXAMPLES:
                stats.examples.append(_redact(value))


def _detect_record_path(
    path: Path, source_format: Literal["json", "jsonl"], record_path: str
) -> Any:
    """根数组自动按元素探查（不物化整份文档：只探测首字节）。

    返回的 CompiledPath 用于流式迭代；profile.record_path 仍保持调用方
    声明的名义路径（默认 "$"），供映射建议与模板使用。
    """
    if source_format == "json" and record_path == "$":
        with path.open("rb") as handle:
            head = handle.read(64)
        for byte in head:
            ch = chr(byte)
            if ch.isspace():
                continue
            if ch == "[":
                return compile_path("$[*]")
            return compile_path("$")
    return compile_path(record_path)


def profile_source(
    path: Path,
    source_format: Literal["json", "jsonl"],
    *,
    sample_limit: int = 1000,
    record_path: str = "$",
) -> SourceProfile:
    """确定性、有界探查。

    - 默认 `$`：根数组按元素探查（等价 `$[*]`），根对象为单条记录。
    - 采样策略：前 800 条全量 + 后续 200 条确定性采样，再往后的流按
      固定种子做 reservoir 替换（每个位置 1/(n+1) 概率进入采样槽），
      因此统计有界（约 sample_limit 条），却仍能发现尾部（近 EOF）的结构变化；
      同一文件多次探查结果完全一致。
    - record_count 为文件实际记录总数（流式统计，不物化）。
    """
    if sample_limit < 2:
        raise ValueError("sample_limit 至少为 2")

    compiled = _detect_record_path(path, source_format, record_path)
    profiler = _Profiler()
    head = min(_HEAD_RECORDS, max(sample_limit - _TAIL_RECORDS, 1))
    reservoir = sample_limit - head
    reservoir_values: list[Any] = []
    rng = random.Random(0)
    total = 0
    try:
        for index, record in enumerate(iter_source(path, source_format, compiled)):
            total = index + 1
            if index < head:
                profiler.observe_record(record.value)
            elif index < head + reservoir:
                reservoir_values.append(record.value)
            else:
                slot = rng.randint(0, index)
                if slot < reservoir:
                    reservoir_values[slot] = record.value
        # 末尾统一观察 reservoir 槽位，保证观察次数严格有界（≈ sample_limit）
        for value in reservoir_values:
            profiler.observe_record(value)
    except (SourceSyntaxError, PathSyntaxError) as exc:
        raise SourceSyntaxError(f"探查失败: {exc}") from exc

    return SourceProfile(
        record_count=total,
        paths=dict(profiler.paths),
        source_format=source_format,
        record_path=record_path,
    )


def schema_fingerprint(profile: SourceProfile) -> str:
    """结构指纹：只依据路径、容器形态与字段类型，不含任何实际值。

    相同结构不同值 → 相同指纹；结构变化（增删路径/形态/类型变化）→ 不同指纹。
    """
    payload = {
        "format": profile.source_format,
        "record_path": profile.record_path,
        "paths": {
            path: {"container": stats.container, "types": sorted(stats.types)}
            for path, stats in sorted(profile.paths.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Compatibility:
    """新旧结构兼容性结论。"""

    kind: Literal["identical", "compatible", "breaking"]
    differences: tuple[str, ...] = ()


def compatibility(old: SourceProfile, new: SourceProfile) -> Compatibility:
    """比较两处探查结果。

    - 指纹一致 → identical。
    - 仅新增路径 → compatible（可复用原模板）。
    - 路径消失或容器/类型变化 → breaking（必须重新确认）。
    """
    if schema_fingerprint(old) == schema_fingerprint(new):
        return Compatibility("identical")

    differences: list[str] = []
    for path in old.paths:
        if path not in new.paths:
            differences.append(f"removed:{path}")
        else:
            a, b = old.paths[path], new.paths[path]
            if a.container != b.container:
                differences.append(f"changed:{path}:{a.container}->{b.container}")
            elif a.types != b.types:
                differences.append(f"changed:{path}:types")
    for path in new.paths:
        if path not in old.paths:
            differences.append(f"added:{path}")

    breaking = any(d.startswith(("removed:", "changed:")) for d in differences)
    return Compatibility("breaking" if breaking else "compatible", tuple(differences))
