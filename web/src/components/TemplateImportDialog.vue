<template>
  <el-dialog
    :model-value="visible"
    title="模板导入"
    width="640px"
    :close-on-click-modal="busy === null"
    :close-on-press-escape="busy === null"
    :show-close="busy === null"
    append-to-body
    @update:model-value="handleCloseRequest"
    @closed="reset"
  >
    <!-- 1. 选择文件 -->
    <div v-if="phase === 'pick'" class="template-import">
      <div
        class="template-import__drop"
        :class="{ 'template-import__drop--dragging': dragging }"
        @dragenter.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @dragover.prevent="dragging = true"
        @drop.prevent="handleDrop"
        @click="fileInput?.click()"
        role="button"
        tabindex="0"
        :aria-label="'拖拽 JSON/JSONL 文件到此处，或点击选择'"
        @keydown.enter="fileInput?.click()"
      >
        <input
          ref="fileInput"
          type="file"
          multiple
          accept=".json,.jsonl"
          class="template-import__input"
          @change="handleInputChange"
        />
        <el-icon class="template-import__drop-icon" aria-hidden="true"><UploadFilled /></el-icon>
        <p class="template-import__drop-title">{{ dragging ? '松开以添加文件' : '拖拽 JSON/JSONL 文件到此处，或点击选择' }}</p>
        <p class="template-import__drop-hint">支持 .json / .jsonl，多个文件将使用同一个模板批量入库</p>
      </div>
      <p v-if="errorText" class="template-import__error" role="alert">{{ errorText }}</p>

      <ul v-if="files.length" class="template-import__files">
        <li v-for="(file, i) in files" :key="`${file.name}-${file.size}-${i}`" class="template-import__file">
          <span class="template-import__file-name" :title="file.name">{{ file.name }}</span>
          <span class="template-import__file-size kpi-num">{{ formatSize(file.size) }}</span>
          <button type="button" class="template-import__file-remove btn-press" :disabled="busy !== null" :aria-label="`移除 ${file.name}`" @click="removeFile(i)">
            移除
          </button>
        </li>
      </ul>
    </div>

    <!-- 2. 上传中 -->
    <div v-else-if="phase === 'upload'" class="template-import template-import--center">
      <el-icon class="is-loading template-import__loading" aria-hidden="true"><Loading /></el-icon>
      <p>正在上传 {{ uploadIndex }}/{{ files.length }}：{{ uploadingName }}</p>
    </div>

    <!-- 3. 选择模板 -->
    <div v-else-if="phase === 'template'" class="template-import">
      <p class="template-import__intro">已上传 {{ uploadedDocs.length }} 个文件，请选择开发人员配置好的导入模板。</p>
      <p v-if="kbDefaultVersionId && defaultTemplateSelected" class="template-import__default-hint">
        当前知识库默认模板已自动选中，可直接继续。
      </p>
      <p v-else-if="kbDefaultVersionId && !defaultTemplateSelected" class="template-import__default-hint template-import__default-hint--warn">
        当前知识库默认模板不适用于 {{ sourceFormat }} 文件，请手动选择模板。
      </p>
      <p v-if="errorText" class="template-import__error" role="alert">{{ errorText }}</p>

      <el-radio-group v-model="selectedVersionId" class="template-import__templates">
        <el-radio
          v-for="opt in templateOptions"
          :key="opt.versionId"
          :value="opt.versionId"
          class="template-import__template"
        >
          <span class="template-import__template-name">{{ opt.templateName }}</span>
          <span class="template-import__template-meta">
            v{{ opt.version }} · {{ opt.sourceFormat }}
            <template v-if="opt.usageCount > 0"> · 已用 {{ opt.usageCount }} 次</template>
          </span>
        </el-radio>
      </el-radio-group>

      <div v-if="templateOptions.length === 0" class="template-import__empty">
        没有可用的 {{ sourceFormat }} 模板。请联系开发人员在「映射模板」中配置，或改用高级导入。
      </div>
    </div>

    <!-- 4. 确认入库 -->
    <div v-else-if="phase === 'confirm'" class="template-import">
      <div class="template-import__summary">
        <div class="template-import__summary-item">
          <span class="template-import__summary-label">文件数</span>
          <span class="template-import__summary-value kpi-num">{{ uploadedDocs.length }}</span>
        </div>
        <div class="template-import__summary-item">
          <span class="template-import__summary-label">预计记录数</span>
          <span class="template-import__summary-value kpi-num">{{ previewTotal }}</span>
        </div>
        <div class="template-import__summary-item">
          <span class="template-import__summary-label">模板</span>
          <span class="template-import__summary-value">{{ selectedOption?.templateName }} v{{ selectedOption?.version }}</span>
        </div>
      </div>
      <p v-if="errorText" class="template-import__error" role="alert">{{ errorText }}</p>
      <p v-if="skippedCount > 0" class="template-import__skip-hint">
        其中 {{ skippedCount }} 个文件没有可导入的 QA 单元，将自动跳过并删除临时上传。
      </p>
      <p class="template-import__confirm-hint">确认后将为每个文件直接创建入库任务，不再需要手动配置字段映射。</p>
    </div>

    <template #footer>
      <div class="template-import__footer">
        <el-button v-if="phase !== 'pick' && phase !== 'upload'" class="btn-press" :disabled="busy !== null" @click="handleBack">
          返回
        </el-button>
        <span class="template-import__footer-spacer" aria-hidden="true"></span>
        <el-button class="btn-press" :disabled="busy !== null" @click="handleCloseRequest">
          {{ phase === 'pick' && files.length === 0 ? '取消' : '关闭' }}
        </el-button>
        <el-button
          v-if="phase === 'pick'"
          type="primary"
          class="btn-press"
          :disabled="files.length === 0 || busy !== null"
          :loading="busy === 'upload'"
          @click="startUpload"
        >
          上传文件
        </el-button>
        <el-button
          v-else-if="phase === 'template'"
          type="primary"
          class="btn-press"
          :disabled="selectedVersionId === null || busy !== null"
          :loading="busy === 'preview'"
          @click="loadPreviews"
        >
          预览记录数
        </el-button>
        <el-button
          v-else-if="phase === 'confirm'"
          type="primary"
          class="btn-press"
          :disabled="uploadedDocs.length === 0 || busy !== null"
          :loading="busy === 'ingest'"
          @click="confirmIngest"
        >
          确认入库
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, UploadFilled } from '@element-plus/icons-vue'
import { deleteDoc, uploadDoc } from '../api/docs'
import { getKb, type KbInfo } from '../api/kb'
import { listMappingTemplates, previewJson, startJsonIngest } from '../api/structured'
import type { MappingTemplateSummary } from '../types/structured'

const props = defineProps<{
  visible: boolean
  kbId: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'ingested', value: { docId: number; jobId: number; name?: string; fileSizeBytes?: number | null }): void
}>()

type Phase = 'pick' | 'upload' | 'template' | 'confirm'
type Busy = 'upload' | 'preview' | 'ingest' | null

interface UploadedDoc {
  file: File
  docId: number
}

interface TemplateOption {
  templateId: number
  templateName: string
  versionId: number
  version: number
  sourceFormat: 'json' | 'jsonl'
  usageCount: number
  mapping: MappingTemplateSummary['versions'][number]['mapping']
}

const phase = ref<Phase>('pick')
const busy = ref<Busy>(null)
const files = ref<File[]>([])
const uploadedDocs = ref<UploadedDoc[]>([])
const templates = ref<MappingTemplateSummary[]>([])
const selectedVersionId = ref<number | null>(null)
const previewTotals = ref<number[]>([])
const errorText = ref('')
const dragging = ref(false)
const kb = ref<KbInfo | null>(null)

const fileInput = ref<HTMLInputElement | null>(null)

const sourceFormat = computed<'json' | 'jsonl' | null>(() => {
  if (files.value.length === 0) return null
  const first = files.value[0].name.toLowerCase()
  const format = first.endsWith('.jsonl') ? 'jsonl' : first.endsWith('.json') ? 'json' : null
  if (format === null) return null
  return files.value.every((f) =>
    format === 'jsonl' ? f.name.toLowerCase().endsWith('.jsonl') : f.name.toLowerCase().endsWith('.json'),
  )
    ? format
    : null
})

const kbDefaultVersionId = computed(() => kb.value?.default_mapping_version_id ?? null)
const defaultTemplateSelected = computed(
  () => kbDefaultVersionId.value !== null && selectedVersionId.value === kbDefaultVersionId.value,
)

const filteredTemplates = computed(() =>
  sourceFormat.value === null
    ? []
    : templates.value.filter((t) => t.source_format === sourceFormat.value),
)

const templateOptions = computed<TemplateOption[]>(() =>
  filteredTemplates.value.flatMap((t) =>
    t.versions.map((v) => ({
      templateId: t.id,
      templateName: t.name,
      versionId: v.id,
      version: v.version,
      sourceFormat: t.source_format,
      usageCount: v.usage_count,
      mapping: v.mapping,
    })),
  ),
)

const selectedOption = computed<TemplateOption | null>(
  () => templateOptions.value.find((opt) => opt.versionId === selectedVersionId.value) ?? null,
)

const uploadIndex = computed(() => {
  const total = files.value.length
  const done = uploadedDocs.value.length
  return Math.min(done + 1, total)
})
const uploadingName = computed(() => files.value[uploadedDocs.value.length]?.name ?? '')

const previewTotal = computed(() => previewTotals.value.reduce((sum, n) => sum + n, 0))
const skippedCount = computed(() => previewTotals.value.filter((n) => n === 0).length)

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function extensionOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx < 0 ? '' : name.slice(idx).toLowerCase()
}

function handleInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) addFiles(input.files)
  input.value = ''
}

function handleDrop(e: DragEvent) {
  dragging.value = false
  const list = e.dataTransfer?.files
  if (list?.length) addFiles(list)
}

function addFiles(list: FileList | File[]) {
  const next = Array.from(list).filter((f) => {
    const ext = extensionOf(f.name)
    return ext === '.json' || ext === '.jsonl'
  })
  if (next.length !== Array.from(list).length) {
    errorText.value = '仅支持 .json / .jsonl 文件'
  } else {
    errorText.value = ''
  }
  files.value = [...files.value, ...next]
  uploadedDocs.value = []
  previewTotals.value = []
  phase.value = 'pick'
}

function removeFile(index: number) {
  files.value = files.value.filter((_, i) => i !== index)
  uploadedDocs.value = []
  previewTotals.value = []
  errorText.value = ''
}

async function startUpload() {
  if (files.value.length === 0 || busy.value !== null) return
  if (sourceFormat.value === null) {
    errorText.value = '请选择同一类型的 JSON 或 JSONL 文件'
    return
  }
  errorText.value = ''
  busy.value = 'upload'
  phase.value = 'upload'
  const docs: UploadedDoc[] = []
  try {
    for (const file of files.value) {
      try {
        const result = await uploadDoc(props.kbId, file)
        docs.push({ file, docId: result.doc_id })
      } catch (err) {
        errorText.value = `${file.name} 上传失败：${getErrorMessage(err)}`
        // 失败时清空本次选择，避免部分文件已上传造成重复；用户可重新选择
        files.value = []
        uploadedDocs.value = []
        phase.value = 'pick'
        busy.value = null
        return
      }
    }
    uploadedDocs.value = docs
    if (!kb.value) {
      try {
        kb.value = await getKb(props.kbId)
      } catch {
        kb.value = null
      }
    }
    await loadTemplates()
    preselectTemplate()
    phase.value = 'template'
  } catch (err) {
    errorText.value = `加载模板失败：${getErrorMessage(err)}`
    files.value = []
    uploadedDocs.value = []
    phase.value = 'pick'
  } finally {
    busy.value = null
  }
}

async function loadTemplates() {
  templates.value = await listMappingTemplates()
}

function preselectTemplate() {
  selectedVersionId.value = null
  const options = templateOptions.value
  if (options.length === 0) return
  const defaultMatch = kbDefaultVersionId.value
    ? options.find((opt) => opt.versionId === kbDefaultVersionId.value)
    : null
  selectedVersionId.value = defaultMatch?.versionId ?? options[0].versionId
}

async function loadPreviews() {
  const option = selectedOption.value
  if (!option || uploadedDocs.value.length === 0 || busy.value !== null) return
  errorText.value = ''
  busy.value = 'preview'
  try {
    const totals: number[] = []
    for (const doc of uploadedDocs.value) {
      const resp = await previewJson(props.kbId, doc.docId, option.mapping, 1)
      totals.push(resp.total_rows)
    }
    previewTotals.value = totals
    phase.value = 'confirm'
  } catch (err) {
    errorText.value = `模板预览失败：${getErrorMessage(err)}`
  } finally {
    busy.value = null
  }
}

async function confirmIngest() {
  const option = selectedOption.value
  if (!option || uploadedDocs.value.length === 0 || busy.value !== null) return
  errorText.value = ''
  busy.value = 'ingest'
  let submitted = 0
  let skipped = 0
  let failed = 0
  let firstError = ''
  try {
    for (let i = 0; i < uploadedDocs.value.length; i++) {
      const doc = uploadedDocs.value[i]
      // 预览为 0 条记录的文件没有可导入内容，自动跳过并删除临时上传
      if ((previewTotals.value[i] ?? 0) === 0) {
        skipped += 1
        try {
          await deleteDoc(props.kbId, doc.docId)
        } catch {
          // 删除失败不阻断后续文件
        }
        continue
      }
      try {
        const result = await startJsonIngest(props.kbId, {
          doc_id: doc.docId,
          mapping_version_id: option.versionId,
        })
        submitted += 1
        emit('ingested', {
          docId: result.doc_id,
          jobId: result.job_id,
          name: doc.file.name,
          fileSizeBytes: doc.file.size,
        })
      } catch (err) {
        failed += 1
        if (!firstError) firstError = getErrorMessage(err)
      }
    }
    const skipText = skipped > 0 ? `，跳过 ${skipped} 个空文件` : ''
    if (failed === 0 && submitted > 0) {
      ElMessage.success(`已提交 ${submitted} 个文档的入库任务${skipText}`)
      emit('update:visible', false)
    } else if (failed === 0 && submitted === 0) {
      ElMessage.warning(`没有可导入的 QA 单元${skipText}`)
      emit('update:visible', false)
    } else {
      errorText.value = `${failed} 个文档入库失败：${firstError}${skipText}`
    }
  } finally {
    busy.value = null
  }
}

function handleBack() {
  errorText.value = ''
  if (phase.value === 'template') {
    phase.value = 'pick'
    uploadedDocs.value = []
    previewTotals.value = []
    return
  }
  if (phase.value === 'confirm') {
    phase.value = 'template'
    previewTotals.value = []
  }
}

function handleCloseRequest() {
  if (busy.value !== null) return
  emit('update:visible', false)
}

function reset() {
  phase.value = 'pick'
  busy.value = null
  files.value = []
  uploadedDocs.value = []
  templates.value = []
  selectedVersionId.value = null
  previewTotals.value = []
  errorText.value = ''
  dragging.value = false
}

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object') {
    const response = err as { error?: { message?: unknown; code?: unknown }; message?: unknown }
    const nested = response.error?.message ?? response.error?.code
    if (typeof nested === 'string' && nested) return nested
    const message = response.message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请重试'
}

watch(
  () => props.visible,
  (v) => {
    if (!v) return
    reset()
    void getKb(props.kbId)
      .then((info) => {
        kb.value = info
      })
      .catch(() => {
        kb.value = null
      })
  },
)
</script>

<style scoped>
.template-import {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.template-import--center {
  align-items: center;
  justify-content: center;
  min-height: 180px;
  color: var(--text-secondary);
  font-size: 13px;
}

.template-import__drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 20px;
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  cursor: pointer;
  text-align: center;
}

.template-import__drop:hover,
.template-import__drop:focus-visible {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 4%, transparent);
}

.template-import__drop--dragging {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 8%, transparent);
}

.template-import__input {
  display: none;
}

.template-import__drop-icon {
  font-size: 30px;
  color: var(--accent-blue);
}

.template-import__drop-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.template-import__drop-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.template-import__error {
  margin: 0;
  font-size: 12px;
  color: var(--accent-red);
}

.template-import__files {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  max-height: 180px;
  overflow-y: auto;
}

.template-import__file {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
}

.template-import__file-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-import__file-size {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.template-import__file-remove {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent-blue);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}

.template-import__loading {
  font-size: 28px;
  color: var(--accent-blue);
}

.template-import__intro,
.template-import__default-hint,
.template-import__confirm-hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}

.template-import__intro,
.template-import__confirm-hint {
  color: var(--text-secondary);
}

.template-import__default-hint {
  color: var(--accent-green);
}

.template-import__default-hint--warn {
  color: var(--accent-orange);
}

.template-import__skip-hint {
  margin: 0;
  font-size: 12px;
  color: var(--accent-orange);
}

.template-import__templates {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.template-import__template {
  display: flex;
  align-items: center;
  width: 100%;
  margin-right: 0;
  padding: 10px 12px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
  height: auto;
}

.template-import__template :deep(.el-radio__label) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  white-space: normal;
}

.template-import__template-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.template-import__template-meta {
  font-size: 12px;
  color: var(--text-tertiary);
}

.template-import__empty {
  padding: 20px;
  border-radius: var(--radius-xl);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 13px;
  text-align: center;
}

.template-import__summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.template-import__summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px;
  border-radius: var(--radius-xl);
  background: var(--bg-subtle);
}

.template-import__summary-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.template-import__summary-value {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-all;
}

.template-import__footer {
  display: flex;
  align-items: center;
  gap: 8px;
}

.template-import__footer-spacer {
  flex: 1;
}
</style>
