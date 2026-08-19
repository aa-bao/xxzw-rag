<!-- 视频数据库：本机历史视频解析任务库，支持搜索/筛选/排序/删除 -->
<template>
  <div class="page library-page">
    <header class="page__header">
      <h1 class="page__title">视频数据库</h1>
      <span class="page__subtitle">本机已解析的视频任务，共 {{ totalCount }} 条</span>
    </header>

    <!-- 工具栏：搜索 + 来源/时间筛选 + 排序 -->
    <section class="toolbar card glass-surface" aria-label="筛选">
      <el-input
        v-model="search"
        class="toolbar__search"
        placeholder="搜索标题 / 摘要 / 来源 / 任务名"
        clearable
        :prefix-icon="Search"
      />
      <el-select v-model="sourceFilter" class="toolbar__select" aria-label="来源筛选">
        <el-option label="全部来源" value="all" />
        <el-option label="微信视频号" value="weixin" />
        <el-option label="B站" value="bilibili" />
        <el-option label="抖音" value="douyin" />
        <el-option label="YouTube" value="youtube" />
        <el-option label="本地视频" value="local" />
        <el-option label="其他" value="other" />
      </el-select>
      <el-select v-model="timeFilter" class="toolbar__select" aria-label="时间筛选">
        <el-option label="全部时间" value="all" />
        <el-option label="今天" value="today" />
        <el-option label="近 7 天" value="7d" />
        <el-option label="近 30 天" value="30d" />
      </el-select>
      <el-select v-model="sortBy" class="toolbar__select" aria-label="排序">
        <el-option label="最新优先" value="newest" />
        <el-option label="最旧优先" value="oldest" />
        <el-option label="名称 A-Z" value="name" />
      </el-select>
      <span v-if="filteredCount !== totalCount" class="toolbar__hint">筛选出 {{ filteredCount }} 条</span>
    </section>

    <section v-if="loading" class="library-empty">
      <el-icon class="empty-icon" aria-hidden="true"><Loading /></el-icon>
      <p class="empty-text">正在读取视频数据库…</p>
    </section>

    <section v-else-if="!tasks.length" class="library-empty">
      <el-icon class="empty-icon" aria-hidden="true"><FolderOpened /></el-icon>
      <p class="empty-text">暂无历史解析任务，去「视频分析 agent」解析第一个视频吧</p>
    </section>

    <section v-else-if="!filteredTasks.length" class="library-empty">
      <el-icon class="empty-icon" aria-hidden="true"><Search /></el-icon>
      <p class="empty-text">没有匹配的视频</p>
      <el-button class="btn-press" @click="clearFilters">清除筛选</el-button>
    </section>

    <section v-else class="library-grid">
      <article
        v-for="task in filteredTasks"
        :key="task.task_id"
        class="lib-card glass-surface"
      >
        <div class="lib-card__cover" role="button" tabindex="0" @click="openTask(task)" @keyup.enter="openTask(task)">
          <img
            v-if="coverSrc(task) && !brokenCovers.has(task.task_id)"
            :src="coverSrc(task)"
            :alt="task.title"
            loading="lazy"
            class="lib-card__img"
            @error="markBroken(task.task_id)"
          />
          <div v-else class="lib-card__noimg">
            <el-icon aria-hidden="true"><VideoCamera /></el-icon>
          </div>
          <span v-if="task.duration_seconds" class="lib-card__duration">{{ formatDuration(task.duration_seconds) }}</span>
          <span class="lib-card__source">{{ sourceLabel(task.source, task.source_kind) }}</span>
        </div>
        <div class="lib-card__body">
          <h3 class="lib-card__title" :title="task.title" role="button" tabindex="0" @click="openTask(task)" @keyup.enter="openTask(task)">{{ task.title }}</h3>
          <p v-if="task.summary" class="lib-card__summary">{{ task.summary }}</p>
          <div class="lib-card__meta">
            <span class="lib-card__date">{{ formatTime(task.created_at) }}</span>
            <div class="lib-card__tags">
              <el-tag v-if="task.has_report" size="small" type="primary" class="lib-card__tag">报告</el-tag>
              <el-tag v-if="(task.keyframes || []).length" size="small" class="lib-card__tag lib-card__tag--kf">{{ task.keyframes.length }} 帧</el-tag>
            </div>
          </div>
        </div>
        <button class="lib-card__delete btn-press" :title="'删除 ' + task.title" @click="confirmDelete(task)">
          <el-icon aria-hidden="true"><Delete /></el-icon>
        </button>
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
          <span class="meta-item"><b>时间：</b>{{ formatTime(activeTask.created_at) }}</span>
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
          <el-button type="danger" plain class="btn-press" @click="confirmDelete(activeTask)">
            <el-icon class="btn-icon" aria-hidden="true"><Delete /></el-icon>
            删除该任务
          </el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Document, FolderOpened, Loading, Search, VideoCamera } from '@element-plus/icons-vue'
import { deleteLibraryTask, libraryFrameUrl, libraryReportUrl, listLibrary, type LibraryTask } from '../api/video'

const tasks = ref<LibraryTask[]>([])
const loading = ref(true)
const detailVisible = ref(false)
const activeTask = ref<LibraryTask | null>(null)

// 筛选状态
const search = ref('')
const sourceFilter = ref<'all' | 'weixin' | 'bilibili' | 'douyin' | 'youtube' | 'local' | 'other'>('all')
const timeFilter = ref<'all' | 'today' | '7d' | '30d'>('all')
const sortBy = ref<'newest' | 'oldest' | 'name'>('newest')
const brokenCovers = ref<Set<string>>(new Set())

const totalCount = computed(() => tasks.value.length)

const filteredTasks = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const dayMs = 24 * 3600 * 1000

  let list = tasks.value.filter((t) => {
    // 关键词：标题/摘要/来源/任务名
    if (keyword) {
      const haystack = [t.title, t.summary, t.source, t.task_id].join(' ').toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    // 来源分类
    if (sourceFilter.value !== 'all') {
      const kind = t.source_kind || classifySource(t.source)
      if (kind !== sourceFilter.value) return false
    }
    // 时间
    if (timeFilter.value !== 'all') {
      const ts = parseTs(t.created_at)
      if (ts == null) return timeFilter.value !== 'today' // 无时间只保留非「今天」
      const diff = startOfToday - ts
      if (timeFilter.value === 'today' && (diff > 0 || ts >= startOfToday + dayMs)) return false
      if (timeFilter.value === '7d' && diff >= 7 * dayMs) return false
      if (timeFilter.value === '30d' && diff >= 30 * dayMs) return false
    }
    return true
  })

  // 排序
  list = [...list]
  if (sortBy.value === 'newest') {
    list.sort((a, b) => (parseTs(b.created_at) ?? 0) - (parseTs(a.created_at) ?? 0))
  } else if (sortBy.value === 'oldest') {
    list.sort((a, b) => (parseTs(a.created_at) ?? 0) - (parseTs(b.created_at) ?? 0))
  } else {
    list.sort((a, b) => a.title.localeCompare(b.title, 'zh'))
  }
  return list
})

const filteredCount = computed(() => filteredTasks.value.length)

function parseTs(iso: string | null): number | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.getTime()
}

// ── 加载 ──

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

function clearFilters() {
  search.value = ''
  sourceFilter.value = 'all'
  timeFilter.value = 'all'
  sortBy.value = 'newest'
}

// ── 删除 ──

async function confirmDelete(task: LibraryTask) {
  try {
    await ElMessageBox.confirm(
      '确定要删除「' + task.title + '」吗？\n该任务目录将被永久删除（含转录/关键帧/报告），不可恢复。',
      '删除视频库任务',
      { type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await deleteLibraryTask(task.task_id)
    ElMessage.success('已删除')
    tasks.value = tasks.value.filter((t) => t.task_id !== task.task_id)
    if (activeTask.value?.task_id === task.task_id) {
      detailVisible.value = false
      activeTask.value = null
    }
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '删除失败')
  }
}

// ── 详情 ──

function openTask(task: LibraryTask) {
  activeTask.value = task
  detailVisible.value = true
}

function openReport() {
  if (!activeTask.value) return
  window.open(libraryReportUrl(activeTask.value.task_id), '_blank')
}

// ── 展示辅助 ──

function markBroken(taskId: string) {
  const next = new Set(brokenCovers.value)
  next.add(taskId)
  brokenCovers.value = next
}

function coverSrc(task: LibraryTask): string {
  const kf = (task.keyframes || [])[0]
  if (!kf) return ''
  const name = kf.path.split(/[\\/]/).pop() ?? ''
  return name ? libraryFrameUrl(task.task_id, name) : ''
}

function frameUrlFor(task: LibraryTask, index = 0): string {
  const kf = (task.keyframes || [])[index]
  if (!kf) return ''
  const name = kf.path.split(/[\\/]/).pop() ?? ''
  return libraryFrameUrl(task.task_id, name)
}

function classifySource(source: string): string {
  const s = (source || '').trim()
  const m = s.match(/^https?:\/\/([^/]+)/i)
  if (!m) return 'local'
  const host = m[1].toLowerCase()
  if (host.includes('weixin.qq.com') || host.includes('finder.video.qq.com') || host.startsWith('sph')) return 'weixin'
  if (host.includes('bilibili.com') || host.includes('b23.tv')) return 'bilibili'
  if (host.includes('douyin.com') || host.includes('iesdouyin.com')) return 'douyin'
  if (host.includes('youtube.com') || host.includes('youtu.be')) return 'youtube'
  return 'other'
}

function sourceKindLabel(kind: string): string {
  const map: Record<string, string> = {
    weixin: '微信视频号',
    bilibili: 'B站',
    douyin: '抖音',
    youtube: 'YouTube',
    local: '本地视频',
    other: '其他',
  }
  return map[kind] ?? kind
}

function sourceLabel(source: string, kind?: string): string {
  const k = kind || classifySource(source)
  return sourceKindLabel(k)
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return h + 'h ' + String(m % 60).padStart(2, '0') + 'm'
  }
  return m + 'm ' + String(s).padStart(2, '0') + 's'
}

function formatTs(seconds: number | null): string {
  if (seconds == null) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const pad = (n: number) => String(n).padStart(2, '0')
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
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
  margin-bottom: 20px;
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

/* ── 工具栏 ── */
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 20px;
  padding: 14px 16px;
}

.toolbar__search {
  width: 280px;
  flex: 1 1 220px;
  max-width: 420px;
}

.toolbar__select {
  width: 130px;
  flex-shrink: 0;
}

.toolbar__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

/* ── 空状态 ── */
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

/* ── 卡片网格 ── */
.library-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
  padding: 4px;
}

.lib-card {
  position: relative;
  border-radius: var(--radius-3xl);
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
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

.lib-card__source {
  position: absolute;
  left: 10px;
  top: 10px;
  padding: 3px 8px;
  border-radius: var(--radius-full);
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.lib-card__body {
  padding: 14px 18px 16px;
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

.lib-card__tags {
  display: flex;
  gap: 6px;
}

.lib-card__tag--kf {
  --el-tag-bg-color: transparent;
}

/* 删除按钮：hover 显示 */
.lib-card__delete {
  position: absolute;
  right: 10px;
  top: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: var(--radius-full);
  background: rgba(176, 42, 42, 0.88);
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, transform 0.15s ease;
  font-size: 14px;
}

.lib-card:hover .lib-card__delete,
.lib-card:focus-within .lib-card__delete {
  opacity: 1;
}

.lib-card__delete:hover {
  transform: scale(1.1);
  background: #8f2222;
}

/* ── 详情弹窗 ── */
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
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding-top: 8px;
}

.btn-icon {
  margin-right: 4px;
}
</style>
