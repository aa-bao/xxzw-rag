/**
 * 结构化 JSON 入库共享类型。
 * 字段全部 snake_case，镜像后端 /api/kb/{kb_id}/json/* 与 /api/mapping-templates 契约。
 * 不做任何转换/JSONPath 求值：本前端只提交声明，由后端执行。
 */

/** 字段角色：与后端 FieldMapping.role 完全一致（8 个） */
export type FieldRole = 'id' | 'title' | 'content' | 'keyword' | 'filter' | 'timestamp' | 'display' | 'ignore'

/** 字段映射：path 为受限 JSONPath 子集（如 $.body / comments[*].text） */
export interface FieldMapping {
  path: string
  role: FieldRole
  name: string
  transforms: TransformSpec[]
  required: boolean
}

/** 固定转换名：后端转换注册表允许的完整名称（对应 8 类转换） */
export type TransformName =
  | 'trim'
  | 'normalize_whitespace'
  | 'normalize_newlines'
  | 'strip_html'
  | 'join'
  | 'to_string'
  | 'to_number'
  | 'to_boolean'
  | 'parse_datetime'
  | 'deduplicate'
  | 'remove_child_echo'

export interface TransformSpec {
  name: TransformName
  args?: Record<string, unknown>
}

/** 切块策略 */
export type ChunkPolicy = 'semantic' | 'atomic' | 'topic' | 'parent-only' | 'ignore'

/** 通用关系规则：源字段匹配最近前序同级记录的目标字段（仅下拉选择，禁止表达式） */
export interface RelationRule {
  source: string
  target: string
  strategy: 'nearest_previous_sibling'
}

/** 一种记录类型：根记录 record_path 为 '$'，子记录为相对路径（如 comments[*]） */
export interface RecordTypeMapping {
  name: string
  record_path: string
  fields: FieldMapping[]
  children: RecordTypeMapping[]
  chunk_policy: ChunkPolicy
  relation_rule: RelationRule | null
}

export interface MappingDefinition {
  source_format: 'json' | 'jsonl'
  record_types: RecordTypeMapping[]
}

/* ── 结构探查 ── */

/** 推断类型（观察到的类型；mixed = 混合/多为 null） */
export type InferredType = 'string' | 'number' | 'boolean' | 'object' | 'array' | 'null' | 'mixed'

export interface ProfileField {
  path: string
  inferred_type: InferredType
  /** 脱敏样例（最多 200 字符） */
  sample: string | null
}

export interface ProfileCandidate {
  /** 记录路径（$ / $.posts[*] / comments[*]） */
  record_path: string
  /** 记录数估计 */
  record_count_estimate: number
  /** 字段数 */
  field_count: number
  /** 嵌套数组数 */
  nested_array_count: number
  /** 置信度 0..1 */
  confidence: number
  /** 最多 5 个脱敏样例 */
  samples: string[]
  fields: ProfileField[]
  /** 该候选路径的确定性草稿映射（选择候选时采用） */
  suggested_mapping: MappingDefinition
}

export interface SourceProfile {
  source_format: 'json' | 'jsonl'
  doc_id: number
  /** 候选记录路径 */
  candidates: ProfileCandidate[]
  /** 结构指纹（仅路径/容器形态/字段类型，不含值） */
  fingerprint: string
  total_records_estimate: number
  sampled_records: number
  warnings: string[]
}

/* ── 兼容性 ── */

export type CompatibilityKind = 'fingerprint_match' | 'compatible' | 'breaking'

export interface Compatibility {
  kind: CompatibilityKind
  /** 新增可选字段（compatible 时列出） */
  added_optional_fields: string[]
  /** 消失的路径（breaking） */
  removed_paths: string[]
  /** 类型/层级变化的路径（breaking） */
  changed_paths: string[]
  detail: string | null
}

/* ── 预览 ── */

export interface PreviewWarning {
  message: string
  source_pointer: string | null
}

/** 后端真实预览的一行：与 MappedRecord 对应 */
export interface PreviewRow {
  record_id: string
  parent_id: string | null
  record_type: string
  title: string
  content: string
  /** 实际送入 embedding 的文本 */
  embedding_text: string
  lexical: {
    title: string
    keywords: string[]
    content: string
  }
  filters: Record<string, unknown>
  timestamps: Record<string, unknown>
  display: Record<string, unknown>
  /** 逐记录原始 JSON 快照 */
  raw: Record<string, unknown>
  source_pointer: string
  warnings: PreviewWarning[]
}

export interface PreviewResponse {
  rows: PreviewRow[]
  /** 预览级警告（非逐记录） */
  warnings: string[]
  total_rows: number
  limit: number | null
}

/* ── 模板与版本 ── */

export interface MappingVersionSummary {
  id: number
  version: number
  mapping: MappingDefinition
  fingerprint: string
  usage_count: number
  created_at: string | null
  created_by: string | null
}

export interface MappingTemplateSummary {
  id: number
  name: string
  source_format: 'json' | 'jsonl'
  current_version: number
  fingerprint: string
  usage_count: number
  created_at: string | null
  updated_at: string | null
  versions: MappingVersionSummary[]
}

export interface CreateMappingTemplateVersionInput {
  /** 传入 = 在该模板下创建新版本；null = 新建模板（name 必填） */
  template_id: number | null
  /** 新建模板时的名称 */
  name?: string
  mapping: MappingDefinition
}

export interface CreateMappingTemplateVersionResult {
  template_id: number
  mapping_version_id: number
  version: number
  fingerprint: string
}

/* ── 入库 ── */

export interface StartJsonIngestInput {
  doc_id: number
  mapping_version_id: number
}

export interface IngestResult {
  doc_id: number
  job_id: number
  mapping_version_id: number
  status: string
}

/* ── 逐记录错误 ── */

export interface MappingErrorEntry {
  source_pointer: string
  record_type: string | null
  severity: 'error' | 'warning'
  message: string
}

export interface MappingErrorsResponse {
  doc_id: number
  entries: MappingErrorEntry[]
  error_count: number
  warning_count: number
  generated_at: string | null
}
