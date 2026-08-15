<template>
  <div class="page">
    <header class="page__header">
      <h1 class="page__title">映射模板</h1>
      <p class="page__hint">
        JSON 结构化入库的映射模板与版本。使用中的版本不可修改；从任意版本创建新版本会生成新的版本号。
      </p>
    </header>

    <!-- 首次加载 -->
    <div v-if="loading" class="tpl-state">
      <el-icon class="tpl-state__icon is-loading"><Loading /></el-icon>
      <p class="tpl-state__hint">加载模板中…</p>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="loadFailed" class="tpl-state">
      <el-icon class="tpl-state__icon"><Warning /></el-icon>
      <p class="tpl-state__title">模板加载失败</p>
      <p class="tpl-state__hint">{{ errorMessage }}</p>
      <el-button type="primary" class="btn-press tpl-state__btn" @click="loadTemplates">重试</el-button>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!templates.length" class="tpl-state">
      <el-icon class="tpl-state__icon"><Files /></el-icon>
      <p class="tpl-state__title">还没有映射模板</p>
      <p class="tpl-state__hint">在 JSON 映射向导的「确认入库」步骤保存模板后，会显示在这里。</p>
    </div>

    <!-- 模板列表 -->
    <div v-else class="tpl-list">
      <section
        v-for="t in templates"
        :key="t.id"
        class="tpl-card glass-surface"
        :aria-label="`模板 ${t.name}`"
      >
        <header class="tpl-card__head">
          <div class="tpl-card__title-wrap">
            <span class="tpl-card__name">{{ t.name }}</span>
            <span class="tpl-card__format">{{ t.source_format === 'jsonl' ? 'JSONL' : 'JSON' }}</span>
          </div>
          <div class="tpl-card__meta">
            <span class="tpl-card__meta-item">
              当前版本 <span class="kpi-num">v{{ t.current_version }}</span>
            </span>
            <span class="tpl-card__meta-item" :title="t.fingerprint">
              指纹 <span class="kpi-num tpl-card__fp">{{ t.fingerprint.slice(0, 12) }}…</span>
            </span>
            <span class="tpl-card__meta-item">
              已用于 <span class="kpi-num">{{ t.usage_count }}</span> 次
            </span>
            <span class="tpl-card__meta-item tpl-card__time">创建 {{ formatDateTime(t.created_at) }}</span>
            <span v-if="t.updated_at" class="tpl-card__meta-item tpl-card__time">
              更新 {{ formatDateTime(t.updated_at) }}
            </span>
          </div>
          <el-button
            class="btn-press"
            size="small"
            :disabled="t.versions.length === 0"
            @click="startEdit(t, t.versions[t.versions.length - 1])"
          >
            从最新版本创建
          </el-button>
        </header>

        <!-- 版本列表 -->
        <ul class="tpl-versions">
          <li v-for="v in t.versions" :key="v.version" class="tpl-version">
            <button
              type="button"
              class="tpl-version__head btn-press"
              :aria-expanded="isExpanded(t.id, v.version)"
              :aria-label="`版本 v${v.version} 详情`"
              @click="toggleVersion(t.id, v.version)"
            >
              <el-icon class="tpl-version__arrow" :class="{ 'tpl-version__arrow--open': isExpanded(t.id, v.version) }">
                <ArrowRight />
              </el-icon>
              <span class="tpl-version__num">v{{ v.version }}</span>
              <span class="tpl-version__fp" :title="v.fingerprint">{{ v.fingerprint.slice(0, 12) }}…</span>
              <span class="tpl-version__used">已用于 {{ v.usage_count }} 次</span>
              <span class="tpl-version__by">{{ v.created_by ?? '—' }}</span>
              <span class="tpl-version__time">{{ formatDateTime(v.created_at) }}</span>
            </button>

            <div v-if="isExpanded(t.id, v.version)" class="tpl-version__body">
              <div class="tpl-version__summary">
                <span class="tpl-version__summary-title">字段角色</span>
                <ul class="tpl-version__roles">
                  <li v-for="rt in flattenRecordTypes(v.mapping.record_types)" :key="rt.name">
                    <span class="tpl-version__rt">{{ rt.name }}</span>
                    <span class="tpl-version__policy">{{ policyLabel(rt.chunk_policy) }}</span>
                    <span class="tpl-version__role-text">{{ roleSummary(rt) }}</span>
                  </li>
                </ul>
              </div>
              <div class="tpl-version__json">
                <span class="tpl-version__json-title">映射 JSON（只读）</span>
                <pre class="tpl-version__pre">{{ JSON.stringify(v.mapping, null, 2) }}</pre>
              </div>
              <el-button
                class="btn-press tpl-version__fork"
                size="small"
                @click="startEdit(t, v)"
              >
                以此版本创建新版本
              </el-button>
            </div>
          </li>
        </ul>
      </section>
    </div>

    <!-- 编辑模式：基于所选版本创建新版本（使用中版本不可变） -->
    <el-dialog
      v-model="editVisible"
      :title="editTitle"
      width="760px"
      :close-on-click-modal="!saving"
      :close-on-press-escape="!saving"
      class="tpl-edit"
    >
      <div v-if="editing" class="tpl-edit__body">
        <p class="tpl-edit__note">
          基于模板「{{ editing.template.name }}」的
          <span class="kpi-num">v{{ editing.baseVersion.version }}</span>
          编辑，保存后将生成新的版本号。
        </p>
        <JsonFieldMappingStep
          :profile="EMPTY_PROFILE"
          :mapping="editing.mapping"
          ref="fieldsStepRef"
          @update:mapping="onEditMapping"
        />
        <JsonRelationStep :mapping="editing.mapping" @update:mapping="onEditMapping" />
      </div>
      <template #footer>
        <div class="tpl-edit__footer">
          <el-button :disabled="saving" @click="editVisible = false">取消</el-button>
          <el-button
            type="primary"
            class="btn-press"
            :disabled="!canSave || saving"
            :loading="saving"
            @click="handleSaveVersion"
          >
            {{ saving ? '保存中…' : '保存新版本' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, Files, Loading, Warning } from '@element-plus/icons-vue'
import { createMappingTemplateVersion, listMappingTemplates } from '../api/structured'
import type {
  MappingDefinition,
  MappingTemplateSummary,
  MappingVersionSummary,
  RecordTypeMapping,
  SourceProfile,
} from '../types/structured'
import JsonFieldMappingStep from '../components/json-mapping/JsonFieldMappingStep.vue'
import JsonRelationStep from '../components/json-mapping/JsonRelationStep.vue'

/* ── 列表加载 ── */
const templates = ref<MappingTemplateSummary[]>([])
const loading = ref(true)
const loadFailed = ref(false)
const errorMessage = ref('')

async function loadTemplates() {
  loading.value = true
  loadFailed.value = false
  try {
    templates.value = await listMappingTemplates()
  } catch (err) {
    loadFailed.value = true
    errorMessage.value = getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(loadTemplates)

/* ── 版本展开（只读展示，不可变） ── */
const expanded = ref<Set<string>>(new Set())

function versionKey(templateId: number, version: number): string {
  return `${templateId}:${version}`
}

function isExpanded(templateId: number, version: number): boolean {
  return expanded.value.has(versionKey(templateId, version))
}

function toggleVersion(templateId: number, version: number) {
  const key = versionKey(templateId, version)
  const next = new Set(expanded.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expanded.value = next
}

/* ── 字段角色摘要（与确认入库步骤一致的只读渲染） ── */
function flattenRecordTypes(rts: RecordTypeMapping[]): RecordTypeMapping[] {
  return rts.flatMap((rt) => [rt, ...flattenRecordTypes(rt.children)])
}

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

function roleSummary(rt: RecordTypeMapping): string {
  const roles = rt.fields.filter((f) => f.role !== 'ignore').map((f) => ROLE_SHORT[f.role] ?? f.role)
  return roles.length ? roles.join('/') : '（无索引字段）'
}

/* ── 编辑模式：基于所选版本创建新版本 ── */
interface EditingState {
  template: MappingTemplateSummary
  baseVersion: MappingVersionSummary
  mapping: MappingDefinition
}

const editVisible = ref(false)
const saving = ref(false)
const editing = ref<EditingState | null>(null)
const fieldsStepRef = ref<InstanceType<typeof JsonFieldMappingStep> | null>(null)

/** 模板编辑无源文档：空 profile 使字段展示为「混合」类型、无样例（不可变更新不依赖 profile） */
const EMPTY_PROFILE: SourceProfile = {
  source_format: 'json',
  doc_id: 0,
  candidates: [],
  fingerprint: '',
  total_records_estimate: 0,
  sampled_records: 0,
  warnings: [],
}

const editTitle = computed(() => (editing.value ? `创建新版本：${editing.value.template.name}` : '创建新版本'))

function startEdit(template: MappingTemplateSummary, base: MappingVersionSummary) {
  editing.value = { template, baseVersion: base, mapping: base.mapping }
  editVisible.value = true
}

/** 字段/关系编辑的不可变更新 */
function onEditMapping(mapping: MappingDefinition) {
  if (editing.value) {
    editing.value = { ...editing.value, mapping }
  }
}

const canSave = computed(() => {
  if (!editing.value || saving.value) return false
  return fieldsStepRef.value?.canContinue === true
})

async function handleSaveVersion() {
  const current = editing.value
  if (!current || saving.value) return
  saving.value = true
  try {
    const result = await createMappingTemplateVersion({
      template_id: current.template.id,
      name: undefined,
      mapping: current.mapping,
    })
    ElMessage.success(`已保存新版本 v${result.version}（指纹 ${result.fingerprint.slice(0, 12)}…）`)
    editVisible.value = false
    editing.value = null
    await loadTemplates()
  } catch (err) {
    ElMessage.error(`保存失败：${getErrorMessage(err)}`)
  } finally {
    saving.value = false
  }
}

/* ── 格式化工具 ── */
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object') {
    const e = err as { error?: { code?: string; message?: string }; message?: unknown }
    const message = e.error?.message ?? e.error?.code
    if (typeof message === 'string' && message) return message
    if (typeof e.message === 'string' && e.message) return e.message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请重试'
}
</script>

<style scoped>
.page__hint {
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
}

.tpl-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.tpl-state__icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.tpl-state__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.tpl-state__hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.tpl-state__btn {
  margin-top: 12px;
}

.tpl-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tpl-card {
  padding: 16px 18px;
  border-radius: var(--radius-3xl);
}

.tpl-card__head {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.tpl-card__title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tpl-card__name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
}

.tpl-card__format {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
}

.tpl-card__meta {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
  flex-wrap: wrap;
}

.tpl-card__meta-item {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.tpl-card__fp {
  font-family: var(--font-mono);
  font-size: 11px;
}

.tpl-card__time {
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.tpl-versions {
  list-style: none;
  margin-top: 12px;
  border-top: 1px solid var(--border-subtle);
}

.tpl-version + .tpl-version {
  border-top: 1px solid var(--border-subtle);
}

.tpl-version__head {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 4px;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
}

.tpl-version__head:hover {
  color: var(--text-primary);
}

.tpl-version__arrow {
  font-size: 13px;
  transition: transform 0.15s ease;
  flex-shrink: 0;
}

.tpl-version__arrow--open {
  transform: rotate(90deg);
}

.tpl-version__num {
  font-weight: 700;
  color: var(--accent-indigo);
  font-variant-numeric: tabular-nums;
}

.tpl-version__fp {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
}

.tpl-version__used {
  color: var(--text-tertiary);
}

.tpl-version__by {
  color: var(--text-tertiary);
}

.tpl-version__time {
  margin-left: auto;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.tpl-version__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0 4px 12px 26px;
}

.tpl-version__summary-title,
.tpl-version__json-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.tpl-version__roles {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}

.tpl-version__roles li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.tpl-version__rt {
  font-weight: 600;
  color: var(--text-primary);
}

.tpl-version__policy {
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
  font-size: 11px;
}

.tpl-version__role-text {
  color: var(--text-tertiary);
}

.tpl-version__pre {
  margin: 6px 0 0;
  padding: 12px 14px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
}

.tpl-version__fork {
  align-self: flex-start;
}

.tpl-edit__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 60vh;
  overflow-y: auto;
}

.tpl-edit__note {
  font-size: 12px;
  color: var(--text-secondary);
}

.tpl-edit__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
</style>
