<!-- 视频数据库：本机历史视频解析任务库（C:\Users\Win10\quick-watch） -->
<template>
  <div class="page library-page">
    <header class="page__header">
      <h1 class="page__title">视频数据库</h1>
      <span class="page__subtitle">本机已解析的视频任务，共 {{ tasks.length }} 条</span>
    </header>

    <section v-if="loading" class="library-empty">
      <el-icon class="empty-icon" aria-hidden="true"><Loading /></el-icon>
      <p class="empty-text">正在读取视频数据库…</p>
    </section>

    <section v-else-if="!tasks.length" class="library-empty">
      <el-icon class="empty-icon" aria-hidden="true"><FolderOpened /></el-icon>
      <p class="empty-text">暂无历史解析任务，去「视频分析 agent」解析第一个视频吧</p>
    </section>

    <section v-else class="library-grid">
      <article
        v-for="task in tasks"
        :key="task.task_id"
        class="lib-card glass-surface btn-press"
        @click="openTask(task)"
      >
        <div class="lib-card__cover">
          <img
            v-if="task.keyframes.length"
            :src="frameUrlFor(task)"
            :alt="task.title"
            loading="lazy"
            class="lib-card__img"
          />
          <div v-else class="lib-card__noimg">
            <el-icon aria-hidden="true"><VideoCamera /></el-icon>
          </div>
          <span v-if="task.duration_seconds" class="lib-card__duration">{{ formatDuration(task.duration_seconds) }}</span>
        </div>
        <div class="lib-card__body">
          <h3 class="lib-card__title" :title="task.title">{{ task.title }}</h3>
          <p v-if="task.summary" class="lib-card__summary">{{ task.summary }}</p>
          <div class="lib-card__meta">
            <span class="lib-card__date">{{ formatTime(task.created_at) }}</span>
            <el-tag v-if="task.has_report" size="small" type="primary" class="lib-card__tag">报告</el-tag>
          </div>
        </div>
      </article>
    </section>

    <!-- 任务详情弹窗 -->
    <el-dialog
      v-model="detailVisible"
      :title="activeTask?.title || '任务详情'"
      width="720px"
      class="lib-dialog"
      destroy-on-close
    >
      <div v-if="activeTask" class="lib-detail">
        <div class="detail-meta">
          <span class="meta-item"><b>来源：</b>{{ activeTask.source || '未知' }}</span>
          <span class="meta-item" v-if="activeTask.duration_seconds"><b>时长：</b>{{ formatDuration(activeTask.duration_seconds) }}</span>
          <span class="meta-item" v-if="activeTask.transcript_source"><b>转录：</b>{{ activeTask.transcript_source }}</span>
        </div>

        <div v-if="activeTask.summary" class="detail-block">
          <h4 class="block-title">摘要</h4>
          <p class="block-text">{{ activeTask.summary }}</p>
        </div>

        <div v-if="activeTask.keypoints.length" class="detail-block">
          <h4 class="block-title">要点</h4>
          <ul class="block-list">
            <li v-for="(kp, i) in activeTask.keypoints" :key="i" class="block-li">{{ kp }}</li>
          </ul>
        </div>

        <div v-if="activeTask.keyframes.length" class="detail-block">
          <h4 class="block-title">关键帧</h4>
          <div class="detail-frames">
            <figure v-for="(kf, i) in activeTask.keyframes.slice(0, 12)" :key="i" class="detail-frame">
              <img :src="frameUrlFor(activeTask, i)" :alt="'帧 ' + (i + 1)" loading="lazy" class="detail-frame__img" />
              <figcaption class="detail-frame__time">{{ formatTs(kf.timestamp_seconds) }}</figcaption>
            </figure>
          </div>
        </div>

        <div v-if="activeTask.transcript" class="detail-block">
          <h4 class="block-title">转录</h4>
          <pre class="detail-transcript">{{ activeTask.transcript }}</pre>
        </div>

        <div class="detail-actions">
          <el-button v-if="activeTask.has_report" type="primary" class="btn-press" @click="openReport">
            <el-icon class="btn-icon" aria-hidden="true"><Document /></el-icon>
            查看 HTML 报告
          </el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, FolderOpened, Loading, VideoCamera } from '@element-plus/icons-vue'
import { libraryFrameUrl, libraryReportUrl, listLibrary, type LibraryTask } from '../api/video'

const tasks = ref<LibraryTask[]>([])
const loading = ref(true)
const detailVisible = ref(false)
const activeTask = ref<LibraryTask | null>(null)

async function load() {
  loading.value = true
  try {
    tasks.value = await listLibrary()
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '读取视频数据库失败')
  } finally {
    loading.value = false
  }
}

function openTask(task: LibraryTask) {
  activeTask.value = task
  detailVisible.value = true
}

function openReport() {
  if (!activeTask.value) return
  window.open(libraryReportUrl(activeTask.value.task_id), '_blank')
}

function frameUrlFor(task: LibraryTask, index = 0): string {
  const kf = task.keyframes[index]
  if (!kf) return ''
  const name = kf.path.split(/[\\/]/).pop() ?? ''
  return libraryFrameUrl(task.task_id, name)
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return `${h}h ${String(m % 60).padStart(2, '0')}m`
  }
  return `${m}m ${String(s).padStart(2, '0')}s`
}

function formatTs(seconds: number | null): string {
  if (seconds == null) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

onMounted(load)
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

.library-page {
  overflow-y: auto;
}

.library-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 24px;
  gap: 16px;
}

.empty-icon {
  font-size: 48px;
  color: var(--text-tertiary);
}

.empty-text {
  font-size: 14px;
  color: var(--text-secondary);
}

.library-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
  padding: 4px;
}

.lib-card {
  border-radius: var(--radius-3xl);
  overflow: hidden;
  cursor: pointer;
}

.lib-card:hover {
  box-shadow: var(--shadow-card-hover);
  transform: translateY(-2px);
}

.lib-card__cover {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--bg-subtle);
}

.lib-card__img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.lib-card__noimg {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 36px;
  color: var(--text-tertiary);
}

.lib-card__duration {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 3px 8px;
  border-radius: var(--radius-full);
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}

.lib-card__body {
  padding: 16px 20px 18px;
}

.lib-card__title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lib-card__summary {
  margin: 0 0 10px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.lib-card__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.lib-card__date {
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.lib-card__tag {
  flex-shrink: 0;
}

.lib-detail {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--text-secondary);
}

.meta-item b {
  color: var(--text-primary);
  font-weight: 600;
}

.detail-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.block-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.block-text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.block-list {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.block-li {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.detail-frames {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
}

.detail-frame {
  margin: 0;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--border-subtle);
}

.detail-frame__img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
}

.detail-frame__time {
  padding: 3px 6px;
  font-size: 10px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.detail-transcript {
  max-height: 240px;
  overflow-y: auto;
  margin: 0;
  padding: 12px 14px;
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.detail-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}

.btn-icon {
  margin-right: 4px;
}
</style>
