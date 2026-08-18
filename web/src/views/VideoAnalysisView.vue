<!-- 视频解析 agent：提交视频 URL 或本地文件，展示转录/关键帧/摘要，支持问答 -->
<template>
  <div class="page video-page">
    <header class="page__header">
      <h1 class="page__title">视频分析 agent</h1>
      <span class="page__subtitle">粘贴视频链接或上传本地文件，AI 自动转写、抽帧并生成分析报告</span>
    </header>

    <!-- 输入区 -->
    <section class="card glass-surface" aria-label="视频解析输入">
      <el-tabs v-model="inputMode" class="video-tabs">
        <el-tab-pane label="视频链接" name="url">
          <div class="input-row">
            <el-input
              v-model="urlInput"
              placeholder="粘贴视频链接（B站 / 抖音 / 微信视频号 / YouTube 等）"
              clearable
              :disabled="submitting"
              @keyup.enter="handleSubmit"
            />
            <el-select v-model="frames" class="frames-select" aria-label="关键帧数量">
              <el-option :value="0" label="纯音频（无关键帧）" />
              <el-option :value="8" label="8 帧（精简）" />
              <el-option :value="12" label="12 帧（默认）" />
              <el-option :value="24" label="24 帧（详细）" />
            </el-select>
            <el-button
              type="primary"
              class="btn-press"
              :loading="submitting"
              :disabled="!urlInput.trim()"
              @click="handleSubmit"
            >
              <el-icon class="btn-icon" aria-hidden="true"><VideoPlay /></el-icon>
              开始解析
            </el-button>
          </div>
          <div class="input-hint">支持主流视频平台公开链接；解析耗时取决于视频时长。</div>
        </el-tab-pane>

        <el-tab-pane label="本地上传" name="file">
          <el-upload
            drag
            class="video-uploader"
            :auto-upload="false"
            :limit="1"
            accept="video/*,.mp3,.m4a,.aac,.wav"
            :on-change="handleFileChange"
            :on-remove="() => (localFile = null)"
          >
            <el-icon class="uploader-icon" aria-hidden="true"><UploadFilled /></el-icon>
            <div class="uploader-text">拖拽视频/音频文件到此处，或 <em>点击选择</em></div>
            <template #tip>
              <div class="uploader-tip">支持 mp4 / mov / mkv / webm / mp3 / m4a 等，最大 500MB</div>
            </template>
          </el-upload>
          <div v-if="localFile" class="input-row upload-submit">
            <el-select v-model="frames" class="frames-select" aria-label="关键帧数量">
              <el-option :value="0" label="纯音频（无关键帧）" />
              <el-option :value="8" label="8 帧（精简）" />
              <el-option :value="12" label="12 帧（默认）" />
              <el-option :value="24" label="24 帧（详细）" />
            </el-select>
            <el-button
              type="primary"
              class="btn-press"
              :loading="submitting"
              :disabled="!localFile"
              @click="handleFileSubmit"
            >
              <el-icon class="btn-icon" aria-hidden="true"><VideoPlay /></el-icon>
              上传并解析
            </el-button>
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <!-- 最近任务 -->
    <section v-if="tasks.length" class="card glass-surface" aria-label="解析任务">
      <div class="card__head">
        <h2 class="card__title">解析任务</h2>
        <el-button text size="small" class="card__reset btn-press" @click="refreshTasks">
          <el-icon class="btn-icon" aria-hidden="true"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
      <el-table :data="tasks" class="video-table" @row-click="selectTask">
        <el-table-column label="来源" min-width="220">
          <template #default="{ row }">
            <div class="task-source" :title="row.source">
              <el-tag :type="row.kind === 'url' ? 'primary' : 'warning'" size="small">{{ row.kind === 'url' ? '链接' : '文件' }}</el-tag>
              <span class="task-source-text">{{ shorten(row.source) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="right">
          <template #default="{ row }">
            <el-button text size="small" @click.stop="removeTask(row.task_id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 任务详情 -->
    <section v-if="active" class="card glass-surface video-detail" aria-label="解析结果">
      <div class="card__head">
        <h2 class="card__title">解析结果</h2>
        <div class="card__actions">
          <el-button
            v-if="active.status === 'complete'"
            text
            size="small"
            class="btn-press"
            @click="openReport"
          >
            <el-icon class="btn-icon" aria-hidden="true"><Document /></el-icon>
            HTML 报告
          </el-button>
          <el-button text size="small" class="btn-press" @click="refreshActive">
            <el-icon class="btn-icon" aria-hidden="true"><Refresh /></el-icon>
            刷新
          </el-button>
        </div>
      </div>

      <!-- 运行中 -->
      <div v-if="active.status === 'running' || active.status === 'submitted'" class="detail-progress">
        <el-progress :percentage="progressPercent" :indeterminate="true" :duration="2" />
        <p class="detail-stage">{{ stageText(active.stage) }}</p>
        <p class="detail-source">正在解析：{{ shorten(active.source) }}</p>
      </div>

      <!-- 失败 -->
      <div v-else-if="active.status === 'failed'" class="detail-error">
        <el-alert type="error" :closable="false" show-icon>
          <template #title>解析失败</template>
          <p>{{ active.error || '未知错误' }}</p>
        </el-alert>
      </div>

      <!-- 完成 -->
      <div v-else-if="active.status === 'complete'">
        <!-- 元信息 -->
        <div class="detail-meta">
          <span class="meta-item"><b>时长：</b>{{ formatDuration(reportDuration) }}</span>
          <span class="meta-item"><b>转录来源：</b>{{ transcriptSource || '未知' }}</span>
          <span class="meta-item" v-if="costAsr"><b>ASR 成本：</b>¥{{ costAsr }}</span>
        </div>

        <!-- 关键帧时间轴 -->
        <div v-if="active.keyframes.length" class="frame-section">
          <h3 class="section-title">关键帧</h3>
          <div class="frame-grid">
            <figure v-for="(kf, idx) in active.keyframes" :key="idx" class="frame-item">
              <img
                :src="frameUrlFor(kf.path)"
                :alt="'关键帧 ' + (idx + 1)"
                loading="lazy"
                class="frame-img"
              />
              <figcaption class="frame-caption">{{ formatTimestamp(kf.timestamp_seconds) }}</figcaption>
            </figure>
          </div>
        </div>

        <!-- 转录 -->
        <div v-if="active.transcript" class="transcript-section">
          <h3 class="section-title">转录文本</h3>
          <pre class="transcript-body">{{ active.transcript }}</pre>
        </div>

        <!-- 问答 -->
        <div class="qa-section">
          <h3 class="section-title">视频问答</h3>
          <div class="input-row">
            <el-input
              v-model="qaQuestion"
              placeholder="基于转录提问，如：这个视频主要讲了什么？"
              clearable
              :disabled="qaLoading"
              @keyup.enter="handleQa"
            />
            <el-button
              type="primary"
              class="btn-press"
              :loading="qaLoading"
              :disabled="!qaQuestion.trim()"
              @click="handleQa"
            >
              提问
            </el-button>
          </div>
          <div v-if="qaAnswer" class="qa-answer">
            <p class="qa-answer-text">{{ qaAnswer }}</p>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, Refresh, UploadFilled, VideoPlay } from '@element-plus/icons-vue'
import {
  askQuestion,
  deleteTask,
  frameUrl,
  getTask,
  listTasks,
  reportUrl,
  submitFileTask,
  submitUrlTask,
  uploadVideoFile,
  type VideoTask,
} from '../api/video'

const inputMode = ref<'url' | 'file'>('url')
const urlInput = ref('')
const frames = ref(12)
const localFile = ref<File | null>(null)
const submitting = ref(false)

const tasks = ref<VideoTask[]>([])
const active = ref<VideoTask | null>(null)
const qaQuestion = ref('')
const qaAnswer = ref('')
const qaLoading = ref(false)

let pollTimer: number | undefined

// ── 输入提交 ──

async function handleSubmit() {
  const source = urlInput.value.trim()
  if (!source || submitting.value) return
  submitting.value = true
  try {
    const task = await submitUrlTask(source, frames.value)
    urlInput.value = ''
    await refreshTasks()
    selectTask(task)
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '提交失败')
  } finally {
    submitting.value = false
  }
}

function handleFileChange(file: { raw: File }) {
  localFile.value = file.raw
}

async function handleFileSubmit() {
  if (!localFile.value || submitting.value) return
  submitting.value = true
  try {
    const uploaded = await uploadVideoFile(localFile.value)
    const task = await submitFileTask(uploaded.path, frames.value)
    localFile.value = null
    await refreshTasks()
    selectTask(task)
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '上传失败')
  } finally {
    submitting.value = false
  }
}

// ── 任务管理 ──

async function refreshTasks() {
  try {
    tasks.value = await listTasks()
    if (active.value) {
      const fresh = tasks.value.find((t) => t.task_id === active.value!.task_id)
      if (fresh) active.value = fresh
    }
  } catch {
    /* 列表加载失败静默 */
  }
}

async function selectTask(task: VideoTask) {
  active.value = task
  qaAnswer.value = ''
  qaQuestion.value = ''
  if (task.status === 'running' || task.status === 'submitted') {
    startPolling()
  } else {
    stopPolling()
  }
}

async function refreshActive() {
  if (!active.value) return
  try {
    const fresh = await getTask(active.value.task_id)
    active.value = fresh
    const idx = tasks.value.findIndex((t) => t.task_id === fresh.task_id)
    if (idx >= 0) tasks.value[idx] = fresh
    if (fresh.status === 'running' || fresh.status === 'submitted') {
      startPolling()
    } else {
      stopPolling()
    }
  } catch {
    /* 忽略 */
  }
}

async function removeTask(taskId: string) {
  try {
    await deleteTask(taskId)
    tasks.value = tasks.value.filter((t) => t.task_id !== taskId)
    if (active.value?.task_id === taskId) active.value = null
  } catch {
    ElMessage.error('删除失败')
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(refreshActive, 3000)
}

function stopPolling() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

// ── 问答 ──

async function handleQa() {
  const question = qaQuestion.value.trim()
  if (!question || !active.value || qaLoading.value) return
  qaLoading.value = true
  qaAnswer.value = ''
  try {
    const resp = await askQuestion(active.value.task_id, question)
    qaAnswer.value = resp.answer
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '问答失败')
  } finally {
    qaLoading.value = false
  }
}

function openReport() {
  if (!active.value) return
  window.open(reportUrl(active.value.task_id), '_blank')
}

// ── 展示辅助 ──

const reportDuration = computed(() => {
  const r = active.value?.report as Record<string, unknown> | null
  return typeof r?.duration_seconds === 'number' ? (r.duration_seconds as number) : 0
})

const transcriptSource = computed(() => {
  const r = active.value?.report as Record<string, unknown> | null
  return typeof r?.transcript_source === 'string' ? (r.transcript_source as string) : ''
})

const costAsr = computed(() => {
  const c = active.value?.cost as Record<string, unknown> | null
  return typeof c?.estimated_asr_cny === 'number' ? String(c.estimated_asr_cny) : ''
})

const progressPercent = computed(() => {
  const stage = active.value?.stage
  if (stage === 'complete') return 100
  const map: Record<string, number> = {
    submitted: 5, starting: 10, running: 40, task_started: 15,
  }
  return map[stage ?? ''] ?? 45
})

function statusLabel(task: VideoTask): string {
  if (task.status === 'complete') return '完成'
  if (task.status === 'failed') return '失败'
  if (task.status === 'running') return '解析中'
  return '排队中'
}

function statusType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'complete') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function stageText(stage: string | null): string {
  const map: Record<string, string> = {
    starting: '正在启动解析引擎…',
    running: '解析进行中…',
    task_started: '任务已启动…',
    captions_accepted: '已获取平台字幕…',
    audio_downloaded: '音频下载完成…',
    audio_extracted: '音频提取完成，正在转写…',
    frame_extract_failed: '关键帧提取失败（不影响转录）',
    complete: '解析完成',
  }
  return map[stage ?? ''] ?? '解析进行中…'
}

function shorten(source: string): string {
  if (source.length <= 40) return source
  return source.slice(0, 37) + '…'
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatDuration(seconds: number): string {
  if (!seconds) return '未知'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return `${h} 小时 ${String(m % 60).padStart(2, '0')} 分`
  }
  return `${m} 分 ${String(s).padStart(2, '0')} 秒`
}

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function frameUrlFor(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? ''
  return frameUrl(active.value!.task_id, name)
}

onMounted(async () => {
  await refreshTasks()
})

onUnmounted(() => {
  stopPolling()
})
</script>
<style scoped>
.page {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
}

.page__header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.page__title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--text-primary);
}

.page__subtitle {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
}

.video-page {
  overflow-y: auto;
}

.video-tabs {
  --el-tabs-header-height: 44px;
}

.input-row {
  display: flex;
  gap: 12px;
  align-items: center;
}

.frames-select {
  width: 170px;
  flex-shrink: 0;
}

.btn-icon {
  margin-right: 4px;
}

.input-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.video-uploader {
  width: 100%;
  border-radius: var(--radius-2xl);
}

.uploader-icon {
  font-size: 48px;
  color: var(--text-tertiary);
}

.uploader-text {
  margin-top: 10px;
  font-size: 14px;
  color: var(--text-secondary);
}

.uploader-text em {
  color: var(--accent-blue);
  font-style: normal;
}

.uploader-tip {
  font-size: 12px;
  color: var(--text-tertiary);
}

.upload-submit {
  margin-top: 18px;
  justify-content: flex-end;
}

.video-table {
  width: 100%;
  cursor: pointer;
  --el-table-border-color: var(--border-subtle);
  --el-table-header-bg-color: transparent;
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--text-primary) 3%, transparent);
}

.task-source {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.task-source-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--text-secondary);
}

.video-detail {
  margin-top: 20px;
}

.detail-progress {
  padding: 28px 12px;
}

.detail-stage {
  margin-top: 16px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.detail-source {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.detail-error {
  padding: 8px;
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  margin: 14px 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.meta-item b {
  color: var(--text-primary);
  font-weight: 600;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  margin: 22px 0 12px;
}

.frame-section {
  margin-top: 4px;
}

.frame-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}

.frame-item {
  margin: 0;
  border-radius: var(--radius-2xl);
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  background: var(--bg-subtle);
}

.frame-img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
}

.frame-caption {
  padding: 5px 10px;
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}

.transcript-section {
  margin-top: 8px;
}

.transcript-body {
  max-height: 340px;
  overflow-y: auto;
  padding: 16px 18px;
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.qa-section {
  margin-top: 16px;
}

.qa-answer {
  margin-top: 14px;
  padding: 18px 20px;
  border-radius: var(--radius-2xl);
  background: color-mix(in srgb, var(--accent-blue) 6%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent-blue) 18%, transparent);
}

.qa-answer-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.9;
  color: var(--text-primary);
  white-space: pre-wrap;
}
</style>
