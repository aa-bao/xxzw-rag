<template>
  <div class="json-upload" role="region" aria-label="上传 JSON 文件">
    <!-- 拖拽/点击选择 -->
    <div
      class="json-upload__drop"
      :class="{ 'json-upload__drop--dragging': dragging }"
      @dragenter.prevent="dragging = true"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="handleDrop"
      @click="fileInput?.click()"
      role="button"
      tabindex="0"
      :aria-label="'选择 JSON 或 JSONL 文件'"
      @keydown.enter="fileInput?.click()"
    >
      <input
        ref="fileInput"
        type="file"
        accept=".json,.jsonl"
        multiple
        class="json-upload__input"
        @change="handleInputChange"
      />
      <el-icon class="json-upload__icon" aria-hidden="true"><UploadFilled /></el-icon>
      <p class="json-upload__title">选择 JSON / JSONL 文件</p>
      <p class="json-upload__hint">支持多选，最多 20 个，单个最大 100 MB</p>
    </div>

    <!-- 已选文件列表 + 进度 -->
    <div v-if="files.length" class="json-upload__file">
      <div v-for="(f, i) in files" :key="i" class="json-upload__file-head">
        <span class="json-upload__file-name" :title="f.name">{{ f.name }}</span>
        <span class="json-upload__file-size kpi-num">{{ formatSize(f.size) }}</span>
        <button
          type="button"
          class="json-upload__clear btn-press"
          :disabled="busy !== null"
          :aria-label="`移除文件 ${f.name}`"
          @click="emit('clear', i)"
        >
          移除
        </button>
      </div>
      <div v-if="busy !== null" class="json-upload__progress">
        <el-progress
          :percentage="busy === 'upload' ? 50 : 90"
          :stroke-width="4"
          :show-text="false"
        />
        <span class="json-upload__progress-label">
          <el-icon v-if="busy !== null" class="is-loading" aria-hidden="true"><Loading /></el-icon>
          {{ busy === 'upload' ? '上传文件' : '探查结构' }}
        </span>
      </div>
    </div>

    <p v-if="displayError" class="json-upload__error" role="alert">{{ displayError }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { UploadFilled, Loading } from '@element-plus/icons-vue'
import type { BusyOperation } from './useJsonMappingWizard'

const props = defineProps<{
  files: File[]
  busy: BusyOperation | null
  error: string
}>()

const emit = defineEmits<{
  (e: 'pick', files: File[]): void
  (e: 'clear', index: number): void
}>()

const MAX_FILES = 20
const MAX_BYTES = 100 * 1024 * 1024

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const localError = ref('')

/** 父组件错误（error prop）与本地校验错误合并展示 */
const displayError = computed(() => {
  const parts = [props.error, localError.value].filter((s) => s.length > 0)
  return parts.join('\n')
})

function handleDrop(e: DragEvent) {
  dragging.value = false
  const files = Array.from(e.dataTransfer?.files ?? [])
  if (files.length) pick(files)
}

function handleInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (files.length) pick(files)
  input.value = ''
}

function pick(selected: File[]) {
  localError.value = ''
  const messages: string[] = []
  const valid: File[] = []
  for (const f of selected) {
    if (f.size > MAX_BYTES) {
      messages.push(`「${f.name}」超过 100 MB 限制（当前 ${formatSize(f.size)}）`)
    } else {
      valid.push(f)
    }
  }
  const room = Math.max(MAX_FILES - props.files.length, 0)
  const kept = valid.length > room ? valid.slice(0, room) : valid
  if (kept.length < valid.length) {
    messages.push('最多选择 20 个文件（已保留前 20 个）')
  }
  localError.value = messages.join('\n')
  if (kept.length === 0) return
  emit('pick', kept)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style scoped>
.json-upload__drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 20px;
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  cursor: pointer;
  text-align: center;
}

.json-upload__drop:hover,
.json-upload__drop:focus-visible {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 4%, transparent);
}

.json-upload__drop--dragging {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 8%, transparent);
}

.json-upload__input {
  display: none;
}

.json-upload__icon {
  font-size: 32px;
  color: var(--accent-blue);
  margin-bottom: 4px;
}

.json-upload__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.json-upload__hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.json-upload__file {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: var(--radius-xl);
  background: var(--bg-subtle);
}

.json-upload__file-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.json-upload__file-head + .json-upload__file-head {
  margin-top: 8px;
}

.json-upload__file-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.json-upload__file-size {
  font-size: 11px;
  color: var(--text-tertiary);
}

.json-upload__clear {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--accent-blue);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

.json-upload__clear:disabled {
  color: var(--text-tertiary);
  cursor: not-allowed;
}

.json-upload__progress {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.json-upload__progress .el-progress {
  flex: 1;
}

.json-upload__progress-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--accent-blue);
  white-space: nowrap;
}

.json-upload__error {
  margin-top: 10px;
  font-size: 12px;
  color: var(--accent-red);
}
</style>
