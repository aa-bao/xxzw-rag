<template>
  <div class="json-confirm" role="region" aria-label="确认入库">
    <!-- 模板版本选择 -->
    <section class="json-confirm__section">
      <h3 class="json-confirm__title">映射模板版本</h3>
      <el-radio-group v-model="mode" class="json-confirm__mode">
        <el-radio value="new">新建模板</el-radio>
        <el-radio value="existing">更新现有模板</el-radio>
      </el-radio-group>

      <template v-if="mode === 'new'">
        <el-input
          v-model="newName"
          class="json-confirm__input"
          placeholder="模板名称（如：博客文章评论）"
          :aria-label="'新模板名称'"
        />
      </template>
      <template v-else>
        <el-select
          v-model="selectedTemplateId"
          class="json-confirm__input"
          placeholder="选择现有模板"
          :aria-label="'选择模板'"
        >
          <el-option
            v-for="t in templates"
            :key="t.id"
            :value="t.id"
            :label="`${t.name}（v${t.current_version}，用于 ${t.usage_count} 次）`"
          />
        </el-select>
        <p v-if="selectedTemplate" class="json-confirm__note">
          当前版本 v{{ selectedTemplate.current_version }}（
          {{ selectedTemplate.fingerprint.slice(0, 12) }}…）
          <template v-if="compatibility">
            <span v-if="compatibility.kind === 'fingerprint_match'" class="json-confirm__ok">
              与本次映射完全一致：直接复用该版本
            </span>
            <span v-else-if="compatibility.kind === 'compatible'" class="json-confirm__warn">
              兼容：创建新版本（新增可选字段 {{ compatibility.added_optional_fields.length }} 个）
            </span>
            <span v-else class="json-confirm__break">
              破坏性变更：将创建新版本 v{{ selectedTemplate.current_version + 1 }}
            </span>
          </template>
        </p>
      </template>
    </section>

    <!-- 破坏性变更需再次确认 -->
    <section v-if="needsReconfirm" class="json-confirm__section">
      <el-checkbox v-model="breakingConfirmed" class="json-confirm__break-check">
        我了解这是破坏性变更，已确认新的映射与入库效果
      </el-checkbox>
    </section>

    <!-- 入库摘要 -->
    <section class="json-confirm__section">
      <h3 class="json-confirm__title">入库摘要</h3>
      <ul class="json-confirm__summary">
        <template v-for="rt in mapping.record_types" :key="rt.name">
          <li class="json-confirm__summary-item">
            <span class="json-confirm__rt-name">{{ rt.name }}</span>
            <span class="kpi-num">{{ rt.record_path }}</span>
            <span class="json-confirm__policy">{{ policyLabel(rt.chunk_policy) }}</span>
            <span class="json-confirm__roles">
              {{ roleSummary(rt) }}
            </span>
          </li>
          <li
            v-for="child in rt.children"
            :key="child.name"
            class="json-confirm__summary-item json-confirm__summary-item--child"
          >
            <span class="json-confirm__rt-name">{{ child.name }}</span>
            <span class="kpi-num">{{ child.record_path }}</span>
            <span class="json-confirm__policy">{{ policyLabel(child.chunk_policy) }}</span>
            <span class="json-confirm__roles">
              {{ roleSummary(child) }}
            </span>
          </li>
        </template>
      </ul>
      <p class="json-confirm__stats">
        预计记录 <span class="kpi-num">{{ totalRows }}</span> 条 ·
        警告 <span class="kpi-num">{{ warningCount }}</span> 条
        <template v-if="versionToUse">
          · 使用映射版本 <span class="kpi-num">v{{ versionToUse.version }}</span>
        </template>
      </p>
      <p v-if="compatibility && compatibility.kind === 'compatible'" class="json-confirm__note">
        本次映射新增了可选字段（{{ compatibility.added_optional_fields.join('、') || '—' }}），
        不会影响现有文档。
      </p>
    </section>

    <!-- 入库文件（批量导入模式；单文件模式不渲染） -->
    <section v-if="files && files.length > 0" class="json-confirm__section">
      <h3 class="json-confirm__title">入库文件</h3>
      <ul class="json-confirm__files">
        <li v-for="f in files" :key="f.docId" class="json-confirm__file">
          <span class="json-confirm__file-name" :title="f.name">{{ f.name }}</span>
          <span
            v-if="f.status === 'ingested'"
            class="json-confirm__pill json-confirm__pill--ok"
            :title="f.jobId !== null ? `入库任务 #${f.jobId}` : undefined"
          >已入库（任务 #{{ f.jobId }}）</span>
          <span
            v-else-if="f.status === 'failed'"
            class="json-confirm__pill json-confirm__pill--err"
            :title="f.error ?? undefined"
          >失败</span>
          <span v-else class="json-confirm__pill">待处理</span>
        </li>
      </ul>
    </section>

    <!-- 确认勾选 -->
    <label class="json-confirm__final">
      <el-checkbox v-model="checked">
        我已检查预览并确认按此映射入库
      </el-checkbox>
    </label>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { BatchDocEntry } from './useJsonMappingWizard'
import type {
  Compatibility,
  MappingDefinition,
  MappingTemplateSummary,
  MappingVersionSummary,
  RecordTypeMapping,
} from '../../types/structured'

const props = defineProps<{
  mapping: MappingDefinition
  templates: MappingTemplateSummary[]
  totalRows: number
  warningCount: number
  compatibility: Compatibility | null
  /** 批量导入批次（单文件模式缺省）：展示各文件入库状态 */
  files?: BatchDocEntry[]
}>()

const mode = ref<'new' | 'existing'>('new')
const newName = ref('')
const selectedTemplateId = ref<number | null>(null)
const checked = ref(false)
const breakingConfirmed = ref(false)

const selectedTemplate = computed<MappingTemplateSummary | null>(
  () => props.templates.find((t) => t.id === selectedTemplateId.value) ?? null,
)

/** 破坏性变更或当前映射与模板指纹不同 → 需要创建新版本并再次确认 */
const needsReconfirm = computed<boolean>(() => {
  if (mode.value === 'new') return false
  return props.compatibility !== null && props.compatibility.kind !== 'fingerprint_match'
})

/** 直接复用的版本（指纹一致时），否则为 null */
const versionToUse = computed<MappingVersionSummary | null>(() => {
  if (mode.value !== 'existing' || !selectedTemplate.value) return null
  if (props.compatibility?.kind !== 'fingerprint_match') return null
  const latest = selectedTemplate.value.versions[selectedTemplate.value.versions.length - 1]
  return latest ?? null
})

const POLICY_LABELS: Record<RecordTypeMapping['chunk_policy'], string> = {
  semantic: '语义切块',
  atomic: '原子切块',
  topic: '多入口完整主题',
  'parent-only': '并入父记录',
  ignore: '忽略',
}

function policyLabel(p: RecordTypeMapping['chunk_policy']): string {
  return POLICY_LABELS[p]
}

const ROLE_SHORT: Record<string, string> = {
  id: 'ID',
  title: '标题',
  content: '正文',
  keyword: '关键词',
  filter: '筛选',
  timestamp: '时间',
  display: '展示',
}

/** 字段角色摘要：按记录类型统计非 ignore 字段的角色 */
function roleSummary(rt: RecordTypeMapping): string {
  const roles = rt.fields.filter((f) => f.role !== 'ignore').map((f) => ROLE_SHORT[f.role] ?? f.role)
  return roles.length ? roles.join('/') : '（无索引字段）'
}

/** 有效载荷：提交创建/复用模板版本并入库 */
function submitPayload(): { template_id: number | null; name: string | undefined } {
  if (mode.value === 'new') {
    return { template_id: null, name: newName.value.trim() || '未命名模板' }
  }
  return { template_id: selectedTemplateId.value, name: undefined }
}

defineExpose({ checked, breakingConfirmed, submitPayload, versionToUse, needsReconfirm })
</script>

<style scoped>
.json-confirm {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.json-confirm__section {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: 12px 14px;
}

.json-confirm__title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.json-confirm__mode {
  margin-bottom: 10px;
}

.json-confirm__input {
  width: 100%;
}

.json-confirm__note {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.json-confirm__ok {
  color: var(--accent-green);
}

.json-confirm__warn {
  color: var(--accent-orange);
}

.json-confirm__break {
  color: var(--accent-red);
}

.json-confirm__summary {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
}

.json-confirm__summary-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--text-primary);
}

.json-confirm__summary-item--child {
  padding-left: 22px;
}

.json-confirm__rt-name {
  font-weight: 600;
}

.json-confirm__policy {
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
}

.json-confirm__roles {
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.json-confirm__stats {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.json-confirm__files {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
}

.json-confirm__file {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--text-primary);
}

.json-confirm__file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.json-confirm__pill {
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
  white-space: nowrap;
}

.json-confirm__pill--ok {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.json-confirm__pill--err {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

.json-confirm__break-check {
  font-size: 12px;
  color: var(--accent-red);
}

.json-confirm__final {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-primary);
}
</style>
