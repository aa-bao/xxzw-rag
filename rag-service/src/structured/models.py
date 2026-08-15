"""结构化 JSON 入库的契约模型。

所有模型不可变（frozen=True）且拒绝未知字段（extra="forbid"），
与设计文档 4.2 / 5.1 / 5.2 节一致。字段路径采用相对路径：
- 记录路径：根记录绝对（"$" 或 "$.posts[*]"），子记录相对（"comments[*]"）
- 字段路径：相对记录本身（"body"、"comments.author"）
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

Role = Literal[
    "id", "title", "content", "keyword", "filter", "timestamp", "display", "ignore"
]
ConditionKind = Literal[
    "required",
    "min_length",
    "max_length",
    "not_empty",
    "skip_if_only_emoji",
    "skip_if_matches",
]
ChunkPolicy = Literal["semantic", "atomic", "topic", "parent-only", "ignore"]

ROLES: tuple[Role, ...] = (
    "id", "title", "content", "keyword", "filter", "timestamp", "display", "ignore",
)


class FieldCondition(BaseModel):
    """字段校验/跳过条件，声明式参数，不允许可执行代码。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ConditionKind
    value: int | str | None = None

    @model_validator(mode="after")
    def _check_params(self) -> FieldCondition:
        if self.kind in ("min_length", "max_length"):
            if not isinstance(self.value, int) or self.value < 0:
                raise ValueError(f"{self.kind} 需要一个非负整数参数")
        elif self.kind == "skip_if_matches":
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("skip_if_matches 需要一个非空正则字符串")
        elif self.value is not None:
            raise ValueError(f"{self.kind} 不接受参数")
        return self


class FieldMapping(BaseModel):
    """字段映射：相对记录路径 + 角色 + 固定转换 + 条件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    role: Role = "display"
    # 输出元数据键名（filter/timestamp/display 角色），缺省取路径最后一段
    name: str | None = None
    transforms: tuple[str, ...] = ()
    conditions: tuple[FieldCondition, ...] = ()


class RelationRule(BaseModel):
    """通用关系规则：子记录 source_field 的值匹配最近前序同级记录 target_field 的值，
    则该前序同级记录成为该子记录的父/上下文记录（如回复、聊天、工单）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: str
    target_field: str


class RecordTypeMapping(BaseModel):
    """一种记录类型的映射声明。子记录路径相对父记录，如 "comments[*]"。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    record_path: str
    fields: tuple[FieldMapping, ...] = ()
    children: tuple[RecordTypeMapping, ...] = ()
    relations: tuple[RelationRule, ...] = ()
    chunk_policy: ChunkPolicy = "semantic"


class MappingDefinition(BaseModel):
    """版本化映射模板的完整定义（存入 mapping_json）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    source_format: Literal["json", "jsonl"]
    record_types: tuple[RecordTypeMapping, ...] = ()
    # 建议流程产物恒为 True；用户确认（绑定文档）即为确认动作
    requires_user_confirmation: bool = True


class SuggestedField(BaseModel):
    """建议器产出的字段角色建议（含置信度与依据规则 ID）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    role: Role
    name: str
    confidence: float = 1.0
    rule_ids: tuple[str, ...] = ()


class MappingSuggestion(MappingDefinition):
    """自动建议（确定性或 LLM 增强）的载体。

    继承 MappingDefinition 的全部字段；恒 requires_user_confirmation=True，
    必须经用户确认后才能保存为 MappingDefinition / 绑定文档。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[SuggestedField, ...] = ()

    def field(self, path: str) -> SuggestedField:
        for f in self.fields:
            if f.path == path:
                return f
        raise KeyError(path)


class SourceRecord(BaseModel):
    """流式解析产出的单条源记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any
    source_pointer: str


class MappedRecord(BaseModel):
    """标准记录（设计文档 5.1）。raw 保存原始对象快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    parent_id: str | None = None
    record_type: str
    title: str | None = None
    content: str | None = None
    keywords: tuple[str, ...] = ()
    filters: dict[str, Any] = {}
    timestamps: dict[str, str] = {}
    display: dict[str, Any] = {}
    raw: dict[str, Any]
    source_pointer: str
    mapping_version_id: int


class IndexChunk(BaseModel):
    """统一索引块（设计文档 5.2）。embedding/lexical 字段独立，
    双路索引一致性由 ingest_run_id + index_state 门控。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str
    document_id: int
    record_id: str
    parent_id: str | None = None
    record_type: str
    content: str
    embedding_text: str
    lexical_title: str | None = None
    lexical_content: str | None = None
    lexical_keywords: tuple[str, ...] = ()
    filters: dict[str, Any] = {}
    timestamps: dict[str, str] = {}
    source_pointer: str
    mapping_version_id: int
    content_hash: str
    ingest_run_id: str
    index_state: Literal["staging", "active"] = "staging"


class MappingIssue(BaseModel):
    """逐记录错误/警告（写入 mapping-errors.jsonl）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["record_error", "warning"]
    record_type: str
    source_pointer: str
    code: str
    message: str
    field: str | None = None
