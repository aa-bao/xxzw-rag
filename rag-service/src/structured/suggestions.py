"""映射建议：确定性角色规则 + 可选 LLM 语义增强。

确定性规则（ordered，见设计文档 4.5）：
1. 唯一性高且名称像 ID（id/_id/uuid/编号 等）→ id
2. 可解析为时间（datetime_parse_rate ≥ 0.8）→ timestamp
3. 长文本（avg_length ≥ 64）→ content
4. 短且重复的标量/列表 → keyword
5. 其他 → display

LLM 增强只接收路径、类型、统计与 ≤200 字符的脱敏样例（长字符串
样例一律不上送）；输出经 MappingSuggestion 验证，非法角色/超时/
无效 JSON 均回退确定性建议。增强结果恒 requires_user_confirmation=True，
建议永不直接绑定文档。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.structured.models import (
    FieldMapping,
    MappingSuggestion,
    RecordTypeMapping,
    SuggestedField,
)
from src.structured.profiler import PathStats, SourceProfile

logger = logging.getLogger(__name__)

_ROLES = (
    "id", "title", "content", "keyword", "filter", "timestamp", "display", "ignore",
)
_ID_NAME_PATTERN = re.compile(r"(^|[-_.])(id|uuid|guid|key|编号|标识)([-_.]|$)", re.IGNORECASE)
_TITLE_WORDS = ("title", "name", "subject", "主题", "标题")
_LONG_TEXT_MIN = 64
_PROMPT_SCHEMA = (
    "你的输出必须是合法 JSON 对象：{\"name\": str, \"source_format\": "
    "\"json\"|\"jsonl\", \"record_types\": [{\"name\": str, \"record_path\": str, "
    "\"fields\": [{\"path\": str, \"role\": \"id|title|content|keyword|filter|"
    "timestamp|display|ignore\", \"name\": str}], \"chunk_policy\": "
    "\"semantic|atomic|parent-only|ignore\"}], \"requires_user_confirmation\": true}"
)


class SuggestionError(ValueError):
    """建议阶段错误（LLM 响应无法验证时内部使用）。"""


def _default_name(path: str) -> str:
    last = path.rsplit(".", 1)[-1]
    return last.split("[", 1)[0]


def _role_for(stats: PathStats, name: str) -> tuple[str, str]:
    """按顺序规则判定角色，返回 (role, rule_id)。"""
    lowered = name.lower()

    # 1. 唯一 ID：高唯一性 + ID 型名称
    if stats.element_count >= 2 and stats.uniqueness >= 0.95 and (
        _ID_NAME_PATTERN.search(name) or lowered in ("id", "_id", "uid", "uuid")
    ):
        return "id", "unique_identifier"

    # 2. 可解析时间
    if stats.datetime_attempted > 0 and stats.datetime_parse_rate >= 0.8:
        return "timestamp", "datetime_parse_rate"

    # 3. 标题：名称像标题的短标量 → title
    if stats.container == "scalar" and stats.avg_length <= 256 and any(
        w in lowered for w in _TITLE_WORDS
    ):
        return "title", "short_title"

    # 4. 长文本 → content
    if stats.element_count > 0 and stats.avg_length >= _LONG_TEXT_MIN:
        return "content", "long_text"

    # 5. 短且重复的标量/列表 → keyword
    if stats.avg_length <= 32:
        return "keyword", "short_repeated"

    # 6. 其他 → display
    return "display", "display"


def _suggest_fields(paths: dict[str, PathStats]) -> tuple[SuggestedField, ...]:
    """根记录字段建议（跳过子记录路径及其归属的数组路径）。"""
    suggested: list[SuggestedField] = []
    for path in sorted(paths):
        stats = paths[path]
        if stats is None or stats.observed_count == 0:
            continue
        if stats.child_of is not None or stats.child_array_count > 0:
            continue  # 子记录字段 / 子记录数组由 children 处理
        name = _default_name(path)
        role, rule_id = _role_for(stats, name)
        suggested.append(
            SuggestedField(path=path, role=role, name=name, confidence=1.0, rule_ids=(rule_id,))
        )
    return tuple(suggested)


def _child_record_type(paths: dict[str, PathStats], array_name: str) -> RecordTypeMapping:
    """从 child_of 聚合的子路径构造子记录类型 comments[*]。

    子记录字段路径相对子记录（去掉 "comments." 前缀）：如 "author"。
    """
    fields: list[FieldMapping] = []
    prefix = f"{array_name}."
    for path in sorted(paths):
        stats = paths[path]
        if stats is None or stats.child_of != array_name or stats.observed_count == 0:
            continue
        relative = path[len(prefix):] if path.startswith(prefix) else path
        name = _default_name(relative)
        role, _ = _role_for(stats, name)
        fields.append(FieldMapping(path=relative, role=role, name=name))
    return RecordTypeMapping(
        name="comment", record_path=f"{array_name}[*]", fields=tuple(fields)
    )


def suggest_mapping(profile: SourceProfile) -> MappingSuggestion:
    """确定性角色建议（不调用任何外部模型）。"""
    fields = _suggest_fields(profile.paths)
    root_fields = tuple(
        FieldMapping(path=f.path, role=f.role, name=f.name) for f in fields
    )
    # 子记录数组：按 child_of 标记收集（子字段路径相对子记录，如 "author"）
    array_names = sorted(
        {stats.child_of for stats in profile.paths.values() if stats.child_of}
    )
    children = tuple(
        _child_record_type(profile.paths, name) for name in array_names
    )
    root = RecordTypeMapping(
        name="record",
        record_path=profile.record_path,
        fields=root_fields,
        children=children,
    )
    return MappingSuggestion(
        name="自动建议",
        source_format=profile.source_format,
        record_types=(root,),
        fields=fields,
        requires_user_confirmation=True,
    )


def _profile_payload(profile: SourceProfile) -> dict[str, Any]:
    """只含路径、类型、统计与短脱敏样例的结构描述（不含完整原文）。

    长字符串样例（>64 字符，通常为正文）一律不上送，避免泄露正文。
    """
    paths: dict[str, dict[str, Any]] = {}
    for path, stats in sorted(profile.paths.items()):
        paths[path] = {
            "container": stats.container,
            "types": sorted(stats.types),
            "element_count": stats.element_count,
            "null_rate": round(stats.null_rate, 3),
            "uniqueness": round(stats.uniqueness, 3),
            "min_length": stats.length_min,
            "max_length": stats.length_max,
            "avg_length": round(stats.avg_length, 2),
            "datetime_parse_rate": round(stats.datetime_parse_rate, 3),
            "child_array_count": stats.child_array_count,
            # 只保留短样例（≤64 字符）；长字符串样例不上送
            "examples": [e for e in stats.examples if len(e) <= 64][:5],
        }
    return {
        "source_format": profile.source_format,
        "record_path": profile.record_path,
        "record_count_sampled": profile.record_count,
        "paths": paths,
    }


async def enhance_suggestion(
    profile: SourceProfile,
    deterministic: MappingSuggestion,
    chat_client: Any,
) -> MappingSuggestion:
    """可选 LLM 增强；任何失败（超时/无效 JSON/非法角色）→ 确定性建议原样返回。

    chat_client 协议：`await chat_client.chat_json(payload: dict) -> str`，
    收到结构统计 payload（含脱敏样例与 requires_user_confirmation），
    返回模型给出的 JSON 字符串。发送/超时/重试由调用方客户端负责。
    """
    payload = _profile_payload(profile)
    payload["requires_user_confirmation"] = True

    try:
        raw = await chat_client.chat_json(payload)
    except Exception as exc:  # noqa: BLE001 — 任何失败都回退确定性建议
        logger.warning("suggestion enhancement failed: %s", exc)
        return deterministic

    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or "record_types" not in data:
            raise SuggestionError("响应缺少 record_types")
        suggestion = MappingSuggestion.model_validate(data)
        for record_type in suggestion.record_types:
            for f in record_type.fields:
                if f.role not in _ROLES:
                    raise SuggestionError(f"非法角色 {f.role!r}")
    except (json.JSONDecodeError, SuggestionError, ValueError) as exc:
        logger.warning("suggestion enhancement invalid response: %s", exc)
        return deterministic

    # 从增强后的 record_types 派生字段建议列表（含置信度/规则来源）
    derived: list[SuggestedField] = []
    for record_type in suggestion.record_types:
        for f in record_type.fields:
            derived.append(
                SuggestedField(
                    path=f.path,
                    role=f.role,
                    name=f.name or f.path.rsplit(".", 1)[-1],
                    confidence=1.0,
                    rule_ids=("semantic_enhancement",),
                )
            )

    # 增强结果仍是建议：恒要求用户确认
    return suggestion.model_copy(
        update={"fields": tuple(derived), "requires_user_confirmation": True}
    )