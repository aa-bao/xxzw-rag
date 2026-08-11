import type { InferredType, TransformName } from '../../types/structured'

/**
 * 固定转换目录：8 类转换的名称、标签与按推断类型的兼容性过滤。
 * 纯数据/纯函数，供字段映射步骤展示与单测直接验证。
 */

export const TRANSFORM_LABELS: Record<TransformName, string> = {
  trim: '去除首尾空白',
  normalize_whitespace: '归一化空白',
  normalize_newlines: '归一化换行',
  strip_html: '去除 HTML',
  join: '数组合并为文本',
  to_string: '转字符串',
  to_number: '转数字',
  to_boolean: '转布尔',
  parse_datetime: '解析日期',
  deduplicate: '去重',
  remove_child_echo: '移除子记录回声',
}

/** 8 类固定转换：仅展示与观测类型兼容的转换 */
export const TRANSFORMS_BY_TYPE: Record<InferredType, TransformName[]> = {
  string: [
    'trim',
    'normalize_whitespace',
    'normalize_newlines',
    'strip_html',
    'to_number',
    'to_boolean',
    'parse_datetime',
  ],
  number: ['to_string', 'to_boolean'],
  boolean: ['to_string'],
  array: ['join', 'deduplicate', 'remove_child_echo'],
  object: [],
  null: ['to_string'],
  mixed: ['to_string'],
}

/** 某类型字段可添加的转换（排除已应用） */
export function allowedTransformsFor(type: InferredType, applied: TransformName[]): TransformName[] {
  const appliedSet = new Set(applied)
  return TRANSFORMS_BY_TYPE[type].filter((n) => !appliedSet.has(n))
}
