<template>
  <div class="json-fields" role="region" aria-label="字段映射">
    <section
      v-for="rt in allRecordTypes"
      :key="rt.name"
      class="json-fields__rt"
      :aria-label="`记录类型 ${rt.name}`"
    >
      <div class="json-fields__rt-head">
        <span class="json-fields__rt-name">{{ rt.name }}</span>
        <span class="json-fields__rt-path kpi-num" :title="rt.record_path">{{ rt.record_path }}</span>
      </div>

      <ul class="json-fields__rows">
        <li v-for="field in rt.fields" :key="field.path" class="json-fields__row">
          <div class="json-fields__row-main">
            <span class="json-fields__path" :title="field.path">{{ field.path }}</span>
            <span class="json-fields__sample" :title="sampleOf(field.path) ?? undefined">
              {{ sampleOf(field.path) ?? '无样例' }}
            </span>
            <span class="json-fields__type" :class="`json-fields__type--${typeOf(field.path)}`">
              {{ typeLabel(typeOf(field.path)) }}
            </span>
            <el-select
              class="json-fields__role"
              :model-value="field.role"
              :aria-label="`${field.path} 的角色`"
              @update:model-value="setRole(rt, field, $event as FieldRole)"
            >
              <el-option
                v-for="opt in ROLE_OPTIONS"
                :key="opt.value"
                :value="opt.value"
                :label="opt.label"
              />
            </el-select>
            <div class="json-fields__required" :title="'该字段缺失时是否拒绝记录'">
              <el-switch
                :model-value="field.required"
                :aria-label="`${field.path} 是否必填`"
                @update:model-value="setRequired(rt, field, $event as boolean)"
              />
              <span class="json-fields__required-label">必填</span>
            </div>
          </div>

          <div class="json-fields__transforms">
            <span v-for="t in field.transforms" :key="t.name" class="json-fields__transform">
              {{ transformLabel(t.name) }}
              <button
                type="button"
                class="json-fields__transform-remove"
                :aria-label="`移除转换 ${transformLabel(t.name)}`"
                @click="removeTransform(rt, field, t.name)"
              >
                ×
              </button>
            </span>
            <el-select
              class="json-fields__transform-add"
              :model-value="''"
              placeholder="添加转换"
              :aria-label="`给 ${field.path} 添加转换`"
              @update:model-value="addTransform(rt, field, $event)"
            >
              <el-option
                v-for="name in allowedTransforms(field)"
                :key="name"
                :value="name"
                :label="transformLabel(name)"
              />
            </el-select>
          </div>
        </li>
      </ul>
    </section>

    <p v-if="!canContinue" class="json-fields__hint" role="status">
      每个索引记录类型至少需要一个 title 或 content 字段才能继续
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { hasRequiredIndexedFields } from './useJsonMappingWizard'
import { TRANSFORM_LABELS, TRANSFORMS_BY_TYPE, allowedTransformsFor } from './transformCatalog'
import type {
  FieldMapping,
  FieldRole,
  InferredType,
  MappingDefinition,
  RecordTypeMapping,
  SourceProfile,
  TransformName,
} from '../../types/structured'

const props = defineProps<{
  profile: SourceProfile
  mapping: MappingDefinition
}>()

const emit = defineEmits<{
  (e: 'update:mapping', mapping: MappingDefinition): void
}>()

/** 8 个字段角色，标签与后端语义一致 */
const ROLE_OPTIONS: { value: FieldRole; label: string }[] = [
  { value: 'id', label: '记录ID' },
  { value: 'title', label: '标题' },
  { value: 'content', label: '正文' },
  { value: 'keyword', label: '仅关键词检索' },
  { value: 'filter', label: '仅筛选' },
  { value: 'timestamp', label: '时间戳' },
  { value: 'display', label: '展示元数据' },
  { value: 'ignore', label: '忽略' },
]

const TYPE_LABELS: Record<InferredType, string> = {
  string: '字符串',
  number: '数字',
  boolean: '布尔',
  object: '对象',
  array: '数组',
  null: '空',
  mixed: '混合',
}

/** 树形 record_types 展平（含子记录类型），每类一栏 */
function flattenRecordTypes(rts: RecordTypeMapping[]): RecordTypeMapping[] {
  return rts.flatMap((rt) => [rt, ...flattenRecordTypes(rt.children)])
}

const allRecordTypes = computed(() => flattenRecordTypes(props.mapping.record_types))

/** 按路径查找观测类型/样例（profile 候选字段） */
function fieldInfo(path: string): ProfileFieldLite {
  const found = props.profile.candidates.flatMap((c) => c.fields).find((f) => f.path === path)
  return found ?? { inferred_type: 'mixed', sample: null }
}

type ProfileFieldLite = { inferred_type: InferredType; sample: string | null }

function typeOf(path: string): InferredType {
  return fieldInfo(path).inferred_type
}

function sampleOf(path: string): string | null {
  return fieldInfo(path).sample
}

function typeLabel(t: InferredType): string {
  return TYPE_LABELS[t]
}

function transformLabel(name: TransformName): string {
  return TRANSFORM_LABELS[name]
}

function allowedTransforms(field: FieldMapping): TransformName[] {
  return allowedTransformsFor(typeOf(field.path), field.transforms.map((t) => t.name))
}

/* ── 不可变更新：每次编辑产生新的 MappingDefinition 并整体上抛 ── */

/** 在树中按对象身份定位 target，替换为 updated（其余节点原样保留） */
function replaceNode(
  rts: RecordTypeMapping[],
  target: RecordTypeMapping,
  updated: RecordTypeMapping,
): RecordTypeMapping[] {
  return rts.map((r) => {
    if (r === target) return updated
    const children = replaceNode(r.children, target, updated)
    return children === r.children ? r : { ...r, children }
  })
}

function updateField(
  rt: RecordTypeMapping,
  path: string,
  update: (f: FieldMapping) => FieldMapping,
) {
  const updated: RecordTypeMapping = {
    ...rt,
    fields: rt.fields.map((f) => (f.path === path ? update(f) : f)),
  }
  emit('update:mapping', {
    ...props.mapping,
    record_types: replaceNode(props.mapping.record_types, rt, updated),
  })
}

function setRole(rt: RecordTypeMapping, field: FieldMapping, role: FieldRole) {
  updateField(rt, field.path, (f) => ({ ...f, role }))
}

function setRequired(rt: RecordTypeMapping, field: FieldMapping, required: boolean) {
  updateField(rt, field.path, (f) => ({ ...f, required }))
}

function addTransform(rt: RecordTypeMapping, field: FieldMapping, name: unknown) {
  if (typeof name !== 'string' || !name) return
  updateField(rt, field.path, (f) => ({
    ...f,
    transforms: [...f.transforms, { name: name as TransformName }],
  }))
}

function removeTransform(rt: RecordTypeMapping, field: FieldMapping, name: TransformName) {
  updateField(rt, field.path, (f) => ({
    ...f,
    transforms: f.transforms.filter((t) => t.name !== name),
  }))
}

/** 每个索引记录类型至少一个 title/content（供向导禁用「下一步」） */
const canContinue = computed(() => hasRequiredIndexedFields(props.mapping))

defineExpose({ canContinue })
</script>

<style scoped>
.json-fields {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.json-fields__rt {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: 12px 14px;
}

.json-fields__rt-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.json-fields__rt-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.json-fields__rt-path {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.json-fields__rows {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.json-fields__row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
}

.json-fields__row-main {
  display: flex;
  align-items: center;
  gap: 10px;
}

.json-fields__path {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-indigo);
  min-width: 90px;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}

.json-fields__sample {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.json-fields__type {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
  flex-shrink: 0;
}

.json-fields__type--string {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
}

.json-fields__type--number {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.json-fields__type--boolean {
  background: color-mix(in srgb, var(--accent-orange) 12%, transparent);
  color: var(--accent-orange);
}

.json-fields__type--array {
  background: color-mix(in srgb, var(--accent-indigo) 12%, transparent);
  color: var(--accent-indigo);
}

.json-fields__type--object,
.json-fields__type--null,
.json-fields__type--mixed {
  background: color-mix(in srgb, var(--accent-red) 10%, transparent);
  color: var(--accent-red);
}

.json-fields__role {
  width: 130px;
  flex-shrink: 0;
}

.json-fields__required {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.json-fields__required-label {
  font-size: 11px;
  color: var(--text-tertiary);
}

.json-fields__transforms {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.json-fields__transform {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
}

.json-fields__transform-remove {
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}

.json-fields__transform-add {
  width: 110px;
}

.json-fields__hint {
  font-size: 12px;
  color: var(--accent-orange);
}
</style>
