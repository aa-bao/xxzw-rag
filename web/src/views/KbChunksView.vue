<template>
  <section class="kb-chunks" aria-label="文档分块">
    <!-- 顶部工具栏：返回 + 标题 + 搜索 -->
    <header class="kb-chunks__bar">
      <div class="kb-chunks__bar-left">
        <button
          type="button"
          class="kb-chunks__back btn-press"
          aria-label="返回文档列表"
          @click="goBack"
        >
          <el-icon class="kb-chunks__back-icon" aria-hidden="true"><ArrowLeft /></el-icon>
          <span>返回文档</span>
        </button>
        <h1 class="kb-chunks__title">
          <el-icon class="kb-chunks__title-icon" aria-hidden="true"><Document /></el-icon>
          <span class="kb-chunks__title-text">{{ docLabel }}</span>
        </h1>
        <span v-if="!kbLoading && kb" class="kb-chunks__kb-name">{{ kb.name }}</span>
      </div>
      <el-input
        v-model="keyword"
        class="kb-chunks__search"
        placeholder="搜索分块内容"
        clearable
        :prefix-icon="Search"
        aria-label="搜索分块内容"
        @keyup.enter="handleSearch"
        @clear="handleSearch"
      />
    </header>

    <!-- 首次加载 -->
    <div v-if="loading" class="kb-chunks__state">
      <el-icon class="kb-chunks__state-icon is-loading"><Loading /></el-icon>
      <p class="kb-chunks__state-hint">加载中…</p>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="loadFailed" class="kb-chunks__state">
      <el-icon class="kb-chunks__state-icon"><Warning /></el-icon>
      <p class="kb-chunks__state-title">分块加载失败</p>
      <p class="kb-chunks__state-hint">{{ errorMessage }}</p>
      <el-button type="primary" class="btn-press kb-chunks__state-btn" @click="loadChunks">重试</el-button>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!chunks.length" class="kb-chunks__state">
      <el-icon class="kb-chunks__state-icon"><Tickets /></el-icon>
      <p class="kb-chunks__state-title">{{ keyword.trim() ? '没有匹配的分块' : '暂无分块' }}</p>
      <p class="kb-chunks__state-hint">
        {{ keyword.trim() ? '换个关键词试试' : '该文档还没有可查看的分块内容' }}
      </p>
    </div>

    <!-- 分块卡片列表 -->
    <template v-else>
      <div class="kb-chunks__list">
        <article
          v-for="(chunk, i) in chunks"
          :key="chunk.chunk_id"
          class="chunk-card glass-surface"
          v-motion="chunkMotion(i)"
          :hovered="hoverMotion"
        >
          <div class="chunk-card__head">
            <span class="chunk-card__source">
              <el-icon class="chunk-card__source-icon" aria-hidden="true"><Document /></el-icon>
              <span class="chunk-card__source-text">{{ docLabel }}</span>
            </span>
            <span v-if="chunk.page !== null" class="chunk-card__page">
              <el-icon class="chunk-card__page-icon" aria-hidden="true"><Collection /></el-icon>
              <span>第 {{ chunk.page }} 页</span>
            </span>
            <span v-if="chunk.score !== null" class="chunk-card__score" :title="`相似度 ${chunk.score.toFixed(4)}`">
              {{ chunk.score.toFixed(4) }}
            </span>
          </div>
          <p class="chunk-card__content">{{ chunk.content }}</p>
        </article>
      </div>

      <!-- 分页 -->
      <el-pagination
        v-if="total > PAGE_SIZE"
        class="kb-chunks__pager"
        layout="prev, pager, next, total"
        :total="total"
        :page-size="PAGE_SIZE"
        :current-page="page"
        background
        @current-change="handlePageChange"
      />
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Collection, Document, Loading, Search, Tickets, Warning } from '@element-plus/icons-vue'
import { getKb, type KbInfo } from '../api/kb'
import { listChunks, type ChunkInfo } from '../api/retrieval'

const route = useRoute()
const router = useRouter()

const PAGE_SIZE = 10

/* ── 路由参数：kbId 与 docId（兼容数组形态） ── */
const kbId = computed(() => {
  const raw = Array.isArray(route.params.id) ? route.params.id[0] : route.params.id
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
})

const docId = computed(() => {
  const raw = Array.isArray(route.params.docId) ? route.params.docId[0] : route.params.docId
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
})

const docLabel = computed(() => (docId.value === null ? '文档' : `文档 #${docId.value}`))

/* ── 文档所属知识库（用于标题旁展示 KB 名，失败不阻断主流程） ── */
const kb = ref<KbInfo | null>(null)
const kbLoading = ref(true)

async function loadKb() {
  const id = kbId.value
  if (id === null) {
    kbLoading.value = false
    return
  }
  kbLoading.value = true
  try {
    kb.value = await getKb(id)
  } catch {
    kb.value = null
  } finally {
    kbLoading.value = false
  }
}

/* ── 分块列表加载（page / keyword 由 loadChunks 参数带入） ── */
const chunks = ref<ChunkInfo[]>([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const loading = ref(true)
const loadFailed = ref(false)
const errorMessage = ref('')

async function loadChunks() {
  const kb = kbId.value
  const doc = docId.value
  if (kb === null || doc === null) {
    loadFailed.value = true
    errorMessage.value = '无效的文档标识'
    loading.value = false
    return
  }
  loading.value = true
  loadFailed.value = false
  try {
    const result = await listChunks(kb, doc, {
      page: page.value,
      page_size: PAGE_SIZE,
      keyword: keyword.value.trim() || undefined,
    })
    chunks.value = result.items
    total.value = result.total
  } catch (err) {
    loadFailed.value = true
    errorMessage.value = getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

function handlePageChange(next: number) {
  page.value = next
  void loadChunks()
}

function handleSearch() {
  page.value = 1
  void loadChunks()
}

/* ── docId 变化时重置并重新加载（docId 不在 Layout 的 RouterView key 内） ── */
watch(docId, () => {
  page.value = 1
  keyword.value = ''
  void loadChunks()
})

onMounted(() => {
  void loadKb()
  void loadChunks()
})

/* ── 返回文档列表 ── */
function goBack() {
  if (kbId.value === null) {
    router.push({ name: 'kb-docs' })
    return
  }
  router.push({ name: 'kb-docs', params: { id: String(kbId.value) } })
}

/* ── 错误信息提取（api client 抛出的响应体） ── */
function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const message = (err as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '加载失败，请重试'
}

/* ── 动效：卡片进入弹簧错峰（规范 4.1），悬停上浮由 :hovered 接管 ── */
const HOVER_SPRING = { type: 'spring', stiffness: 440, damping: 42 } as const

function chunkMotion(i: number) {
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
.kb-chunks {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

/* ── 顶部工具栏 ── */
.kb-chunks__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.kb-chunks__bar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.kb-chunks__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
}

.kb-chunks__back:hover {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.kb-chunks__back-icon {
  font-size: 16px;
}

.kb-chunks__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.kb-chunks__title-icon {
  font-size: 17px;
  color: var(--accent-blue);
  flex-shrink: 0;
}

.kb-chunks__title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-chunks__kb-name {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-chunks__search {
  width: 240px;
  flex-shrink: 0;
}

/* ── 分块卡片 ── */
.kb-chunks__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chunk-card {
  padding: 16px 20px;
  border-radius: var(--radius-3xl);
}

.chunk-card:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

.chunk-card__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.chunk-card__source {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  flex: 1;
}

.chunk-card__source-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.chunk-card__source-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 来源徽章：页码 */
.chunk-card__page {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-indigo) 10%, transparent);
  color: var(--accent-indigo);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
}

.chunk-card__page-icon {
  font-size: 12px;
}

/* 相似度徽章：蓝色调（分数越高越好） */
.chunk-card__score {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
}

/* 分块内容：多行截断 */
.chunk-card__content {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
  white-space: pre-wrap;
}

/* ── 分页 ── */
.kb-chunks__pager {
  display: flex;
  justify-content: center;
  margin-top: 20px;
}

/* ── 页面级状态（加载/错误/空） ── */
.kb-chunks__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.kb-chunks__state-icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.kb-chunks__state-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.kb-chunks__state-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.kb-chunks__state-btn {
  margin-top: 12px;
}
</style>
