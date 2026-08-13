<template>
  <div class="json-preview" role="region" aria-label="真实预览">
    <!-- 预览错误：留在本步骤并指向出错记录 -->
    <div v-if="error" class="json-preview__error" role="alert">
      <p class="json-preview__error-msg">{{ error.message }}</p>
      <p v-if="error.source_pointer" class="json-preview__error-pointer">
        出错位置：<span class="kpi-num">{{ error.source_pointer }}</span>
      </p>
    </div>

    <template v-else>
      <!-- 记录选择器 -->
      <div class="json-preview__meta">
        <span class="json-preview__count">
          预览 <strong class="kpi-num">{{ rows.length }}</strong>
          <span class="json-preview__count-separator">/</span>
          共 <strong class="kpi-num">{{ totalRows }}</strong> 条
        </span>
        <el-select
          class="json-preview__picker"
          :model-value="selectedIndex"
          aria-label="选择预览记录"
          @update:model-value="selectedIndex = $event as number"
        >
          <el-option
            v-for="(row, i) in rows"
            :key="row.record_id"
            :value="i"
            :label="optionLabel(row, i)"
          />
        </el-select>
      </div>

      <!-- 标签页 -->
      <el-tabs v-model="activeTab" class="json-preview__tabs">
        <el-tab-pane label="原始 JSON" name="raw">
          <pre class="json-preview__pre">{{ prettyRaw }}</pre>
        </el-tab-pane>
        <el-tab-pane label="可读内容" name="readable">
          <div class="json-preview__kv">
            <p><strong>标题</strong>{{ currentRow.title }}</p>
            <p><strong>正文</strong>{{ currentRow.content }}</p>
            <p><strong>记录类型</strong>{{ currentRow.record_type }}</p>
            <p><strong>来源</strong>{{ currentRow.source_pointer }}</p>
            <p v-if="currentRow.parent_id"><strong>父记录</strong>{{ currentRow.parent_id }}</p>
          </div>
        </el-tab-pane>
        <el-tab-pane label="向量文本" name="embedding">
          <pre class="json-preview__pre">{{ currentRow.embedding_text }}</pre>
        </el-tab-pane>
        <el-tab-pane label="词法字段" name="lexical">
          <div class="json-preview__kv">
            <p><strong>标题</strong>{{ currentRow.lexical.title }}</p>
            <p><strong>正文</strong>{{ currentRow.lexical.content }}</p>
            <p><strong>关键词</strong>{{ currentRow.lexical.keywords.join('、') || '（无）' }}</p>
          </div>
        </el-tab-pane>
        <el-tab-pane label="筛选/时间/展示" name="meta">
          <div class="json-preview__kv">
            <p><strong>筛选字段</strong>{{ pretty(currentRow.filters) }}</p>
            <p><strong>时间戳</strong>{{ pretty(currentRow.timestamps) }}</p>
            <p><strong>展示元数据</strong>{{ pretty(currentRow.display) }}</p>
          </div>
        </el-tab-pane>
        <el-tab-pane label="警告" name="warnings">
          <p v-if="currentRow.warnings.length === 0" class="json-preview__none">该记录无警告</p>
          <ul class="json-preview__warnings">
            <li v-for="(w, i) in currentRow.warnings" :key="i" class="json-preview__warning">
              {{ w.message }}
              <span v-if="w.source_pointer" class="kpi-num">{{ w.source_pointer }}</span>
            </li>
          </ul>
          <p v-if="globalWarnings.length" class="json-preview__global">
            预览级警告：{{ globalWarnings.join('；') }}
          </p>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PreviewRow } from '../../types/structured'

const props = defineProps<{
  rows: PreviewRow[]
  totalRows: number
  globalWarnings: string[]
  /** 后端预览错误（留在本步骤展示，前端不重建转换） */
  error: { message: string; source_pointer: string | null } | null
}>()

const selectedIndex = ref(0)
const activeTab = ref('raw')

/** rows 变化（重新预览 / 切换记录）时回到第一行，避免越界 */
watch(
  () => props.rows,
  () => {
    selectedIndex.value = 0
  },
)

const currentRow = computed<PreviewRow>(() => {
  const idx = Math.min(selectedIndex.value, props.rows.length - 1)
  return props.rows[idx] ?? EMPTY_ROW
})

const EMPTY_ROW: PreviewRow = {
  record_id: '',
  parent_id: null,
  record_type: '',
  title: '',
  content: '',
  embedding_text: '',
  lexical: { title: '', keywords: [], content: '' },
  filters: {},
  timestamps: {},
  display: {},
  raw: {},
  source_pointer: '',
  warnings: [],
}

const prettyRaw = computed(() => JSON.stringify(currentRow.value.raw, null, 2))

const RECORD_TYPE_LABELS: Record<string, string> = {
  record: '主记录',
  post: '帖子',
  comment: '评论',
}

function recordTypeLabel(recordType: string): string {
  return RECORD_TYPE_LABELS[recordType] ?? recordType
}

/** 仅转换展示格式；不修改后端 source_pointer，避免影响稳定记录 ID。 */
function readablePath(pointer: string): string {
  if (!pointer || pointer === '$') return '$'
  const normalized = pointer.startsWith('$/') ? pointer.slice(1) : pointer
  const parts = normalized.split('/').filter(Boolean)
  let path = '$'
  for (const part of parts) {
    const decoded = part.replace(/~1/g, '/').replace(/~0/g, '~')
    path += /^\d+$/.test(decoded) ? `[${decoded}]` : `.${decoded}`
  }
  return path
}

function rowSummary(row: PreviewRow): string {
  const raw = row.raw as Record<string, unknown>
  const author = typeof raw.author === 'string' ? raw.author.trim() : ''
  const sourceText =
    row.title ||
    row.content ||
    (typeof raw.body === 'string' ? raw.body : '') ||
    (typeof raw.text === 'string' ? raw.text : '')
  const text = sourceText.replace(/\s+/g, ' ').trim()
  const summary = author && text ? `${author}：${text}` : text || author || '无可读内容'
  return summary.length > 34 ? `${summary.slice(0, 34)}…` : summary
}

function optionLabel(row: PreviewRow, index: number): string {
  return `${index + 1}  ${recordTypeLabel(row.record_type)}  ${rowSummary(row)}  ·  ${readablePath(row.source_pointer)}`
}

function pretty(value: Record<string, unknown>): string {
  const entries = Object.entries(value)
  if (entries.length === 0) return '（无）'
  return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join('；')
}
</script>

<style scoped>
.json-preview__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--text-secondary);
}

.json-preview__picker {
  width: min(520px, calc(100% - 140px));
}

.json-preview__count {
  white-space: nowrap;
  color: var(--text-secondary);
}

.json-preview__count-separator {
  margin: 0 3px;
  color: var(--text-tertiary);
}

.json-preview__pre {
  margin: 0;
  padding: 12px 14px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 40vh;
  overflow: auto;
}

.json-preview__kv {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.json-preview__kv p {
  margin: 0;
  font-size: 12px;
  color: var(--text-primary);
}

.json-preview__kv strong {
  display: inline-block;
  min-width: 72px;
  color: var(--text-tertiary);
  font-weight: 500;
  margin-right: 8px;
}

.json-preview__none {
  font-size: 12px;
  color: var(--text-tertiary);
}

.json-preview__warnings {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.json-preview__warning {
  font-size: 12px;
  color: var(--accent-orange);
}

.json-preview__global {
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-secondary);
}

.json-preview__error {
  padding: 16px;
  border-radius: var(--radius-xl);
  background: color-mix(in srgb, var(--accent-red) 8%, transparent);
}

.json-preview__error-msg {
  margin: 0;
  font-size: 13px;
  color: var(--accent-red);
}

.json-preview__error-pointer {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
