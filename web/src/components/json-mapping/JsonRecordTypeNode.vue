<template>
  <div class="rel-node">
    <div class="rel-node__head">
      <span class="rel-node__name">{{ recordType.name }}</span>
      <span class="rel-node__path kpi-num" :title="recordType.record_path">
        {{ recordType.record_path }}
      </span>
      <el-select
        class="rel-node__policy"
        :model-value="recordType.chunk_policy"
        :aria-label="`${recordType.name} 的切块策略`"
        @update:model-value="setPolicy($event as ChunkPolicy)"
      >
        <el-option v-for="opt in POLICY_OPTIONS" :key="opt.value" :value="opt.value" :label="opt.label" />
      </el-select>
    </div>

    <p v-if="recordType.chunk_policy === 'ignore'" class="rel-node__note rel-node__note--ignore">
      该记录类型不生成记录（关联数据仅保留在父记录内容中）
    </p>
    <p v-else-if="parentIdField" class="rel-node__note">
      父记录 ID 自动写入 <span class="kpi-num">{{ parentIdField }}</span>
    </p>

    <!-- 关系规则：仅下拉选择，禁止表达式 -->
    <div class="rel-node__rule" :aria-label="`${recordType.name} 的关系规则`">
      <span class="rel-node__rule-label">关系规则</span>
      <el-switch
        :model-value="recordType.relation_rule !== null"
        :aria-label="`${recordType.name} 是否启用最近前序同级规则`"
        @update:model-value="setRuleEnabled($event as boolean)"
      />
      <template v-if="recordType.relation_rule !== null">
        <el-select
          class="rel-node__rule-select"
          :model-value="recordType.relation_rule.source"
          aria-label="关系源字段"
          @update:model-value="setRuleSource($event as string)"
        >
          <el-option v-for="f in fields" :key="f.path" :value="f.path" :label="`${f.path}（源）`" />
        </el-select>
        <span class="rel-node__rule-arrow">匹配最近前序同级的</span>
        <el-select
          class="rel-node__rule-select"
          :model-value="recordType.relation_rule.target"
          aria-label="关系目标字段"
          @update:model-value="setRuleTarget($event as string)"
        >
          <el-option v-for="f in fields" :key="f.path" :value="f.path" :label="`${f.path}（目标）`" />
        </el-select>
      </template>
      <span v-else class="rel-node__rule-off">未启用</span>
    </div>

    <!-- 子记录类型 -->
    <ul v-if="recordType.children.length" class="rel-node__children">
      <li v-for="child in recordType.children" :key="child.name" class="rel-node__child">
        <JsonRecordTypeNode
          :record-type="child"
          :ancestors="[...ancestors, recordType]"
          :mapping="mapping"
          @update:mapping="emit('update:mapping', $event)"
        />
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type {
  ChunkPolicy,
  FieldMapping,
  MappingDefinition,
  RecordTypeMapping,
} from '../../types/structured'

const props = defineProps<{
  recordType: RecordTypeMapping
  ancestors: RecordTypeMapping[]
  mapping: MappingDefinition
}>()

const emit = defineEmits<{
  (e: 'update:mapping', mapping: MappingDefinition): void
}>()

const POLICY_OPTIONS: { value: ChunkPolicy; label: string }[] = [
  { value: 'semantic', label: '语义切块' },
  { value: 'atomic', label: '按记录原子切块' },
  { value: 'parent-only', label: '并入父记录' },
  { value: 'ignore', label: '忽略' },
]

/** 本节点字段（编辑父子关系的取值来源；不含 ignore 角色，简化下拉） */
const fields = computed<FieldMapping[]>(() => props.recordType.fields.filter((f) => f.role !== 'ignore'))

/** 父记录 ID 继承：后端会为子记录写入最近祖先的 id 字段 */
const parentIdField = computed<string | null>(() => {
  if (props.ancestors.length === 0) return null
  const idField = props.ancestors[props.ancestors.length - 1].fields.find((f) => f.role === 'id')
  return idField ? idField.path : null
})

/* ── 不可变更新：整树重建并上抛，由向导替换 state.mapping ── */

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

function apply(updated: RecordTypeMapping) {
  emit('update:mapping', {
    ...props.mapping,
    record_types: replaceNode(props.mapping.record_types, props.recordType, updated),
  })
}

function setPolicy(policy: ChunkPolicy) {
  apply({ ...props.recordType, chunk_policy: policy })
}

/** 禁用规则：仅移除 relation_rule，保留其余配置 */
function setRuleEnabled(enabled: boolean) {
  if (enabled) {
    const source = fields.value[0]?.path ?? ''
    const target = fields.value[1]?.path ?? fields.value[0]?.path ?? ''
    if (!source || !target) return
    apply({
      ...props.recordType,
      relation_rule: { source, target, strategy: 'nearest_previous_sibling' },
    })
  } else {
    apply({ ...props.recordType, relation_rule: null })
  }
}

function setRuleSource(source: string) {
  const rule = props.recordType.relation_rule
  if (!rule) return
  apply({
    ...props.recordType,
    relation_rule: { ...rule, source },
  })
}

function setRuleTarget(target: string) {
  const rule = props.recordType.relation_rule
  if (!rule) return
  apply({
    ...props.recordType,
    relation_rule: { ...rule, target },
  })
}
</script>

<style scoped>
.rel-node {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: 12px 14px;
  background: var(--bg-subtle);
}

.rel-node__head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.rel-node__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.rel-node__path {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.rel-node__policy {
  width: 150px;
  margin-left: auto;
}

.rel-node__note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.rel-node__note--ignore {
  color: var(--accent-orange);
}

.rel-node__rule {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.rel-node__rule-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.rel-node__rule-select {
  width: 170px;
}

.rel-node__rule-arrow {
  font-size: 12px;
  color: var(--text-tertiary);
}

.rel-node__rule-off {
  font-size: 12px;
  color: var(--text-tertiary);
}

.rel-node__children {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  padding-left: 16px;
  border-left: 2px solid var(--border-subtle);
}
</style>
