<template>
  <el-dialog
    :model-value="visible"
    title="上传文档"
    width="560px"
    :close-on-click-modal="!batchRunning"
    :close-on-press-escape="!batchRunning"
    @update:model-value="handleClose"
    @closed="handleClosed"
  >
    <!-- 拖拽区：拖文件进来自动入队；点击选择文件 -->
    <div
      class="upload-drop"
      :class="{ 'upload-drop--dragging': dragging }"
      @dragenter.prevent="dragging = true"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="handleDrop"
      @click="fileInput?.click()"
      role="button"
      tabindex="0"
      :aria-label="'拖拽文件到此处，或点击选择文件'"
      @keydown.enter="fileInput?.click()"
    >
      <input
        ref="fileInput"
        type="file"
        multiple
        accept=".txt,.md,.markdown"
        class="upload-drop__input"
        @change="handleInputChange"
      />
      <el-icon class="upload-drop__icon" aria-hidden="true"><UploadFilled /></el-icon>
      <p class="upload-drop__title">{{ dragging ? '松开以添加文件' : '拖拽文件到此处，或点击选择' }}</p>
      <p class="upload-drop__hint">支持 .txt / .md / .markdown，可多选</p>
    </div>

    <!-- 队列列表 -->
    <div v-if="items.length" class="upload-queue">
      <div class="upload-queue__head">
        <span class="upload-queue__summary">
          共 <span class="kpi-num">{{ items.length }}</span> 个
          <span class="upload-queue__ok kpi-num">成功 {{ successCount }}</span>
          <span class="upload-queue__err kpi-num">失败 {{ failCount }}</span>
        </span>
        <el-progress
          class="upload-queue__progress"
          :percentage="percent"
          :stroke-width="4"
          :show-text="false"
        />
      </div>

      <ul class="upload-queue__list">
        <li v-for="(item, i) in items" :key="item.id" class="upload-item">
          <span class="upload-item__name" :title="item.file.name">{{ item.file.name }}</span>
          <span class="upload-item__size kpi-num">{{ formatSize(item.file.size) }}</span>
          <span class="upload-item__status" :class="`upload-item__status--${item.status}`">
            <span v-if="item.status === 'uploading'" class="upload-item__dot" v-motion="breathMotion" aria-hidden="true"></span>
            {{ statusLabel(item.status) }}
          </span>
          <span class="upload-item__error" v-if="item.error" :title="item.error">{{ item.error }}</span>
          <button
            v-if="item.status === 'failed'"
            type="button"
            class="upload-item__retry btn-press"
            :disabled="batchRunning"
            :aria-label="`重新上传 ${item.file.name}`"
            @click="retry(item)"
          >
            重试
          </button>
        </li>
      </ul>
    </div>

    <!-- 底部：关闭/完成 -->
    <template #footer>
      <el-button class="btn-press" :disabled="batchRunning" @click="handleClose">
        {{ failCount > 0 || successCount > 0 ? '关闭' : '取消' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadDoc } from '../api/docs'
import type { DocInfo } from '../api/docs'

/**
 * 上传弹窗：拖拽/选择文件 → 队列显示每文件状态 → 并发上传（最多 3 路）。
 * 上传成功回调 onUploaded(doc)，由父组件追加列表并轮询状态。
 */
interface UploadItem {
  id: number
  file: File
  status: 'pending' | 'uploading' | 'success' | 'failed'
  error: string | null
}

type UploadStatus = UploadItem['status']

const props = defineProps<{
  visible: boolean
  kbId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'uploaded', doc: DocInfo): void
}>()

const UPLOAD_CONCURRENCY = 3
const DONE_HOLD_MS = 1200

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const items = ref<UploadItem[]>([])
const batchRunning = ref(false)
let nextUploadId = 1

const successCount = computed(() => items.value.filter((it) => it.status === 'success').length)
const failCount = computed(() => items.value.filter((it) => it.status === 'failed').length)

const percent = computed(() =>
  items.value.length === 0 ? 0 : Math.round((successCount.value / items.value.length) * 100),
)

const STATUS_LABEL: Record<UploadStatus, string> = {
  pending: '排队中',
  uploading: '上传中',
  success: '成功',
  failed: '失败',
}

function statusLabel(s: UploadStatus): string {
  return STATUS_LABEL[s]
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const breathMotion = {
  initial: { opacity: 0.35 },
  enter: {
    opacity: 1,
    transition: { type: 'tween', duration: 0.9, repeat: Infinity, repeatType: 'reverse', ease: 'easeInOut' },
  },
}

/* ── 文件入队 ── */
function enqueue(files: FileList | File[]) {
  const list = Array.from(files).filter((f) => f.size > 0)
  if (!list.length) return
  items.value = [
    ...items.value,
    ...list.map((f) => ({ id: nextUploadId++, file: f, status: 'pending' as const, error: null })),
  ]
  void drainQueue()
}

function handleDrop(e: DragEvent) {
  dragging.value = false
  const files = e.dataTransfer?.files
  if (files?.length) enqueue(files)
}

function handleInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) enqueue(input.files)
  input.value = ''
}

function handleClose() {
  // 上传中不允许关闭（避免丢文件）
  if (batchRunning.value) return
  emit('update:visible', false)
}

function handleClosed() {
  // 弹窗关闭后清空队列，下次打开是全新批次
  items.value = []
  batchRunning.value = false
}

/* ── 并发上传池 ── */
async function drainQueue() {
  if (batchRunning.value) return
  batchRunning.value = true
  try {
    while (true) {
      const batch = items.value.filter((it) => it.status === 'pending').slice(0, UPLOAD_CONCURRENCY)
      if (batch.length === 0) break
      const batchIds = new Set(batch.map((b) => b.id))
      items.value = items.value.map((it) =>
        batchIds.has(it.id) ? { ...it, status: 'uploading', error: null } : it,
      )
      await Promise.all(batch.map((b) => runUpload(b.id)))
    }
  } finally {
    batchRunning.value = false
    // 全部成功：停留片刻展示汇总
    if (items.value.length > 0 && failCount.value === 0) {
      window.setTimeout(() => {
        if (items.value.every((it) => it.status === 'success')) {
          emit('update:visible', false)
        }
      }, DONE_HOLD_MS)
    }
  }
}

async function runUpload(itemId: number) {
  const kbId = props.kbId
  const item = items.value.find((it) => it.id === itemId)
  if (!item || item.status !== 'uploading') return
  if (kbId === null) {
    items.value = items.value.map((it) =>
      it.id === itemId ? { ...it, status: 'failed', error: '无效的知识库' } : it,
    )
    return
  }
  try {
    const result = await uploadDoc(kbId, item.file)
    const doc: DocInfo = {
      id: result.doc_id,
      title: item.file.name,
      source: '本地上传',
      source_type: 'file',
      status: 'pending',
      chunk_count: 0,
      file_size_bytes: item.file.size,
      error_message: null,
      created_at: new Date().toISOString(),
      ingested_at: null,
    }
    emit('uploaded', doc)
    items.value = items.value.map((it) =>
      it.id === itemId ? { ...it, status: 'success' } : it,
    )
  } catch (err) {
    const e = err as { error?: { code?: string; message?: string } }
    // 409 DOC_DUPLICATE：文件内容已存在，提示更明确
    const message =
      e?.error?.code === 'DOC_DUPLICATE'
        ? `重复文件：${e.error.message ?? '同内容文件已存在'}`
        : e?.error?.message || '上传失败'
    items.value = items.value.map((it) =>
      it.id === itemId ? { ...it, status: 'failed', error: message } : it,
    )
  }
}

function retry(item: UploadItem) {
  items.value = items.value.map((it) =>
    it.id === item.id ? { ...it, status: 'pending', error: null } : it,
  )
  void drainQueue()
}

// 打开弹窗时重置队列（全新批次）
watch(
  () => props.visible,
  (v) => {
    if (v) {
      items.value = []
      batchRunning.value = false
    }
  },
)
</script>

<style scoped>
/* ── 拖拽区 ── */
.upload-drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 36px 20px;
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  cursor: pointer;
  transition: none;
  text-align: center;
}

.upload-drop:hover,
.upload-drop:focus-visible {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 4%, transparent);
}

.upload-drop--dragging {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 8%, transparent);
}

.upload-drop__input {
  display: none;
}

.upload-drop__icon {
  font-size: 32px;
  color: var(--accent-blue);
  margin-bottom: 4px;
}

.upload-drop__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.upload-drop__hint {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ── 队列 ── */
.upload-queue {
  margin-top: 16px;
}

.upload-queue__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.upload-queue__summary {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.upload-queue__ok {
  color: var(--accent-green);
}

.upload-queue__err {
  color: var(--accent-red);
}

.upload-queue__progress {
  flex: 1;
}

.upload-queue__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 240px;
  overflow-y: auto;
  padding: 2px;
}

.upload-queue__list::-webkit-scrollbar {
  width: 6px;
}

.upload-queue__list::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-tertiary) 35%, transparent);
  border-radius: var(--radius-full);
}

/* ── 队列行 ── */
.upload-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-lg);
  background: var(--bg-subtle);
}

.upload-item__name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-item__size {
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.upload-item__status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.upload-item__status--pending {
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
}

.upload-item__status--uploading {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
}

.upload-item__status--success {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.upload-item__status--failed {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

.upload-item__dot {
  width: 5px;
  height: 5px;
  border-radius: var(--radius-full);
  background: currentColor;
}

.upload-item__error {
  max-width: 140px;
  font-size: 11px;
  color: var(--accent-red);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}

.upload-item__retry {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent-blue);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}

.upload-item__retry:disabled {
  color: var(--text-tertiary);
  cursor: not-allowed;
}
</style>
