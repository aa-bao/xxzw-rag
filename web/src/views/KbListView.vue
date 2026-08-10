<template>
  <div class="kb-page">
    <!-- 顶部：大标题 + 搜索 + 新建按钮（仅管理员） -->
    <header class="kb-page__header">
      <h1 class="kb-page__title">知识库</h1>
      <div class="kb-page__actions">
        <el-input
          v-model="keyword"
          class="kb-page__search"
          placeholder="搜索知识库"
          clearable
          :prefix-icon="Search"
          aria-label="搜索知识库"
        />
        <el-button v-if="auth.isAdmin" type="primary" class="btn-press" @click="openCreateDialog">
          <el-icon class="kb-page__new-icon"><Plus /></el-icon>
          新建知识库
        </el-button>
      </div>
    </header>

    <!-- 首次加载 -->
    <div v-if="loading" class="kb-page__state">
      <el-icon class="kb-page__state-icon is-loading"><Loading /></el-icon>
      <p class="kb-page__state-hint">加载中…</p>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="loadFailed" class="kb-page__state">
      <el-icon class="kb-page__state-icon"><Warning /></el-icon>
      <p class="kb-page__state-title">知识库加载失败</p>
      <p class="kb-page__state-hint">{{ errorMessage }}</p>
      <el-button type="primary" class="btn-press kb-page__state-btn" @click="loadKbs">重试</el-button>
    </div>

    <!-- 卡片网格 -->
    <div v-else-if="filteredKbs.length" class="kb-page__grid">
      <article
        v-for="(kb, i) in filteredKbs"
        :key="kb.id"
        class="kb-card glass-surface btn-press"
        role="button"
        tabindex="0"
        :aria-label="auth.isAdmin ? `进入知识库 ${kb.name}` : `与 ${kb.name} 开始对话`"
        v-motion="cardMotion(i)"
        :hovered="hoverMotion"
        @click="openKb(kb)"
        @keydown.enter="openKb(kb)"
        @keydown.space.prevent="openKb(kb)"
      >
        <div class="kb-card__body">
          <!-- 缩略图：有封面显示封面图，无封面用渐变 -->
          <div
            class="kb-card__thumb"
            :style="kb.cover_url ? undefined : { background: thumbnailGradient(kb) }"
            role="img"
            :aria-label="`${kb.name} 封面`"
          >
            <img
              v-if="kb.cover_url"
              :src="kb.cover_url"
              class="kb-card__thumb-img"
              alt=""
            />
            <svg
              v-else
              viewBox="0 0 24 24"
              width="24"
              height="24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
          </div>

          <!-- 标题 + 描述 -->
          <div class="kb-card__content">
            <h3 class="kb-card__title" :title="kb.name">{{ kb.name }}</h3>
            <p v-if="kb.description" class="kb-card__desc" :title="kb.description">
              {{ kb.description }}
            </p>
          </div>

          <!-- 右上角删除（仅管理员） -->
          <button
            v-if="auth.isAdmin"
            type="button"
            class="kb-card__delete btn-press"
            aria-label="删除知识库"
            @click.stop="confirmDelete(kb)"
          >
            <el-icon class="kb-card__delete-icon"><Delete /></el-icon>
          </button>
        </div>

        <!-- 底部元信息 -->
        <footer class="kb-card__meta">
          <span class="kb-card__date">
            <el-icon class="kb-card__meta-icon"><Calendar /></el-icon>
            <time class="kb-card__date-text">{{ formatDate(kb.created_at) }}</time>
          </span>
          <span class="kb-card__pill" :class="pillClass(kb)">{{ pillLabel(kb) }}</span>
        </footer>
      </article>
    </div>

    <!-- 空状态：无匹配 -->
    <div v-else-if="kbs.length" class="kb-page__state">
      <el-icon class="kb-page__state-icon"><FolderOpened /></el-icon>
      <p class="kb-page__state-title">没有匹配的知识库</p>
      <p class="kb-page__state-hint">换个关键词试试</p>
    </div>

    <!-- 空状态：无数据 -->
    <div v-else class="kb-page__state">
      <el-icon class="kb-page__state-icon"><FolderOpened /></el-icon>
      <p class="kb-page__state-title">还没有知识库</p>
      <p class="kb-page__state-hint">
        {{ auth.isAdmin ? '点击「新建知识库」创建第一个' : '请联系管理员创建知识库' }}
      </p>
    </div>

    <!-- 新建知识库弹窗（仅管理员可见） -->
    <el-dialog v-model="createVisible" title="新建知识库" width="420px" @closed="resetCreateForm">
      <el-form :model="createForm" label-position="top" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="给知识库起个名字" />
        </el-form-item>
        <el-form-item label="描述（可选）">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="3"
            placeholder="用一句话描述这个知识库的内容"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button class="btn-press" @click="createVisible = false">取消</el-button>
        <el-button type="primary" class="btn-press" :loading="creating" @click="handleCreate">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Calendar, Delete, FolderOpened, Loading, Plus, Search, Warning } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createKb, deleteKb, listKbs, type KbIndexStatus, type KbInfo } from '../api/kb'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

/* ── 数据加载 ── */
const kbs = ref<KbInfo[]>([])
const loading = ref(true)
const loadFailed = ref(false)
const errorMessage = ref('')

async function loadKbs() {
  loading.value = true
  loadFailed.value = false
  try {
    kbs.value = await listKbs()
  } catch (err) {
    loadFailed.value = true
    errorMessage.value = getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(loadKbs)

/* ── 搜索（前端过滤：按名称/描述） ── */
const keyword = ref('')
const filteredKbs = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return kbs.value
  return kbs.value.filter((kb) => {
    const name = kb.name.toLowerCase()
    const desc = (kb.description ?? '').toLowerCase()
    return name.includes(kw) || desc.includes(kw)
  })
})

/* ── 角色分支：管理员进详情 / 员工进对话 ── */
function openKb(kb: KbInfo) {
  if (auth.isAdmin) {
    router.push({ path: `/kb/${kb.id}` })
  } else {
    router.push({ name: 'chat', query: { kb: String(kb.id) } })
  }
}

/* ── 日期格式化：YYYY-MM-DD（本地工具函数，禁引入库） ── */
function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/* ── 缩略图渐变：按 kb.id 确定性选取（蓝/靛/橙/绿低饱和系） ── */
const THUMB_GRADIENTS = [
  'linear-gradient(135deg, rgba(0, 122, 255, 0.25), rgba(88, 86, 214, 0.15))',
  'linear-gradient(135deg, rgba(88, 86, 214, 0.25), rgba(0, 122, 255, 0.15))',
  'linear-gradient(135deg, rgba(255, 149, 0, 0.22), rgba(255, 59, 48, 0.12))',
  'linear-gradient(135deg, rgba(52, 199, 89, 0.22), rgba(0, 122, 255, 0.12))',
]

function thumbnailGradient(kb: KbInfo): string {
  return THUMB_GRADIENTS[Math.abs(kb.id) % THUMB_GRADIENTS.length]
}

/* ── 索引状态胶囊 ── */
const STATUS_LABEL: Record<KbIndexStatus, string> = {
  ready: '已就绪',
  rebuilding: '重建中',
  failed: '失败',
  deleting: '删除中',
}

function pillClass(kb: KbInfo): string {
  return kb.index_status in STATUS_LABEL
    ? `kb-card__pill--${kb.index_status}`
    : 'kb-card__pill--unknown'
}

function pillLabel(kb: KbInfo): string {
  return STATUS_LABEL[kb.index_status] ?? (kb.index_status || '未知')
}

/* ── 新建知识库弹窗 ── */
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ name: '', description: '' })

function openCreateDialog() {
  createVisible.value = true
}

function resetCreateForm() {
  createForm.name = ''
  createForm.description = ''
}

async function handleCreate() {
  const name = createForm.name.trim()
  if (!name) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  creating.value = true
  try {
    const kb = await createKb({ name, description: createForm.description.trim() || undefined })
    createVisible.value = false
    ElMessage.success('知识库创建成功')
    kbs.value = [...kbs.value, kb]
  } catch (err) {
    ElMessage.error(getErrorMessage(err))
  } finally {
    creating.value = false
  }
}

/* ── 删除：确认后调 deleteKb ── */
async function confirmDelete(kb: KbInfo) {
  try {
    await ElMessageBox.confirm(
      `确定要删除知识库「${kb.name}」吗？其下所有文档与索引将被一并删除，此操作不可恢复。`,
      '删除知识库',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    /* 用户取消，无操作 */
    return
  }
  try {
    await deleteKb(kb.id)
    kbs.value = kbs.value.filter((item) => item.id !== kb.id)
    ElMessage.success('知识库已删除')
  } catch (err) {
    ElMessage.error(getErrorMessage(err))
  }
}

/* ── 错误信息提取（api client 抛出的响应体） ── */
function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const message = (err as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请重试'
}

/* ── 卡片动效：进入弹簧错峰 + 悬停上浮（规范 4.4） ── */
const HOVER_SPRING = { type: 'spring', stiffness: 440, damping: 42 } as const

function cardMotion(i: number) {
  return {
    initial: { y: 8, opacity: 0 },
    enter: {
      y: 0,
      opacity: 1,
      delay: Math.min(i * 30, 300),
      transition: { type: 'spring', stiffness: 250, damping: 25 },
    },
  }
}

const hoverMotion = {
  enter: {
    y: -2,
    transition: HOVER_SPRING,
  },
}
</script>

<style scoped>
.kb-page {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
}

/* ── 顶部 ── */
.kb-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.kb-page__title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--text-primary);
}

.kb-page__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.kb-page__search {
  width: 240px;
}

.kb-page__new-icon {
  margin-right: 6px;
}

/* ── 卡片网格 ── */
.kb-page__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
}

/* ── 卡片 ── */
.kb-card {
  padding: 20px;
  border-radius: var(--radius-3xl);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 16px;
  user-select: none;
}

.kb-card:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

.kb-card__body {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  min-height: 70px;
}

/* 渐变缩略图 */
.kb-card__thumb {
  flex-shrink: 0;
  width: 70px;
  height: 70px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.95);
  overflow: hidden;
}

.kb-card__thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  display: block;
}

/* 标题与描述 */
.kb-card__content {
  flex: 1;
  min-width: 0;
  padding-top: 2px;
}

.kb-card__title {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.3;
  color: var(--text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.kb-card__desc {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

/* 删除按钮：右上角，透明圆角底 */
.kb-card__delete {
  position: absolute;
  top: -8px;
  right: -8px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
}

.kb-card__delete:hover {
  background: rgba(255, 255, 255, 0.6);
  color: var(--accent-red);
}

.kb-card__delete-icon {
  font-size: 14px;
}

/* ── 底部元信息 ── */
.kb-card__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 14px;
  border-top: 1px solid var(--border-subtle);
}

.kb-card__date {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.kb-card__meta-icon {
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.kb-card__date-text {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 索引状态胶囊：状态色 10-12% 透明底 + 同色文字 */
.kb-card__pill {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  line-height: 1.4;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.kb-card__pill--rebuilding {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
}

.kb-card__pill--failed {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

.kb-card__pill--deleting {
  background: color-mix(in srgb, var(--accent-orange) 12%, transparent);
  color: var(--accent-orange);
}

.kb-card__pill--unknown {
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
}

/* ── 页面级状态（加载/错误/空） ── */
.kb-page__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.kb-page__state-icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.kb-page__state-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.kb-page__state-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.kb-page__state-btn {
  margin-top: 12px;
}
</style>
