<template>
  <section class="kb-docs" aria-label="文档列表">
    <!-- 顶部工具栏：标题 + 上传 -->
    <header class="kb-docs__bar">
      <h2 class="kb-docs__title">文档</h2>
      <div class="kb-docs__actions">
        <el-button type="primary" class="btn-press" @click="uploadVisible = true">
          <el-icon class="kb-docs__btn-icon"><Upload /></el-icon>
          上传
        </el-button>
      </div>
    </header>

    <!-- ══ 上传弹窗：拖拽 / 选择文件 → 队列 → 并发上传 ══ -->
    <UploadDialog
      v-model:visible="uploadVisible"
      :kb-id="kbId"
      @uploaded="handleUploaded"
      @open-json-wizard="handleOpenJsonWizard"
    />

    <!-- ══ JSON 映射向导：JSON/JSONL 结构化入库 ══ -->
    <JsonMappingWizard
      v-if="wizardVisible && kbId !== null"
      :visible="wizardVisible"
      :kb-id="kbId"
      @close="wizardVisible = false"
      @ingested="handleWizardIngested"
    />

    <!-- 首次加载 -->
    <div v-if="loading" class="kb-docs__state">
      <el-icon class="kb-docs__state-icon is-loading"><Loading /></el-icon>
      <p class="kb-docs__state-hint">加载中…</p>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="loadFailed" class="kb-docs__state">
      <el-icon class="kb-docs__state-icon"><Warning /></el-icon>
      <p class="kb-docs__state-title">文档加载失败</p>
      <p class="kb-docs__state-hint">{{ errorMessage }}</p>
      <el-button type="primary" class="btn-press kb-docs__state-btn" @click="loadDocs">重试</el-button>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!docs.length" class="kb-docs__state">
      <el-icon class="kb-docs__state-icon"><Files /></el-icon>
      <p class="kb-docs__state-title">还没有文档</p>
      <p class="kb-docs__state-hint">点击「上传」添加第一个文件</p>
    </div>

    <!-- 文档表格 -->
    <div v-else class="kb-docs__table-card glass-surface" v-motion="tableMotion">
      <!-- 筛选区：文件名 / 状态 / 大小 / 上传时间 / Chunk 数，全部前端过滤 -->
      <div class="kb-docs__filter" role="search" aria-label="文档筛选">
        <el-input
          v-model="keyword"
          class="kb-docs__filter-name"
          placeholder="按文件名搜索"
          clearable
          :prefix-icon="Search"
          aria-label="按文件名搜索"
          @input="resetPage"
        />
        <el-select
          v-model="filterStatus"
          class="kb-docs__filter-select"
          placeholder="状态"
          clearable
          aria-label="按状态筛选"
          @change="resetPage"
        >
          <el-option
            v-for="opt in STATUS_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-select
          v-model="filterSize"
          class="kb-docs__filter-select"
          placeholder="大小"
          clearable
          aria-label="按大小筛选"
          @change="resetPage"
        >
          <el-option
            v-for="opt in SIZE_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-select
          v-model="filterTime"
          class="kb-docs__filter-select"
          placeholder="上传时间"
          clearable
          aria-label="按上传时间筛选"
          @change="resetPage"
        >
          <el-option
            v-for="opt in TIME_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-select
          v-model="filterChunks"
          class="kb-docs__filter-select"
          placeholder="Chunk"
          clearable
          aria-label="按切片数筛选"
          @change="resetPage"
        >
          <el-option
            v-for="opt in CHUNK_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <span class="kb-docs__filter-count kpi-num">
          共 {{ docs.length }} 条 / 筛选后 {{ filteredCount }}
        </span>
        <button
          type="button"
          class="kb-docs__filter-reset btn-press"
          :disabled="!hasActiveFilters"
          aria-label="重置筛选条件"
          @click="resetFilters"
        >
          <el-icon aria-hidden="true"><RefreshLeft /></el-icon>
          重置
        </button>

        <!-- 批量操作（并入筛选区，选中时出现，不挤表格）：选中项为多个时显示批量操作 -->
        <template v-if="selectedDocIds.size > 0">
          <span class="kb-docs__batch-sep" aria-hidden="true"></span>
          <span class="kb-docs__batch-count kpi-num">已选 {{ selectedDocIds.size }}</span>
          <button
            type="button"
            class="doc-action doc-action--primary btn-press"
            :disabled="batchBusy"
            @click="batchReindex"
          >
            批量重建
          </button>
          <button
            type="button"
            class="doc-action doc-action--danger btn-press"
            :disabled="batchBusy"
            @click="batchDelete"
          >
            批量删除
          </button>
          <button
            type="button"
            class="doc-action btn-press"
            @click="clearSelection"
          >
            取消
          </button>
        </template>
      </div>

      <el-table
        ref="tableRef"
        :data="pagedDocs"
        row-key="id"
        empty-text="没有匹配的文档"
        class="kb-docs__table"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="42" />
        <!-- 文件名：唯一弹性列，自动吃满剩余空间（其他列固定，总宽 = 容器宽） -->
        <el-table-column label="文件名" min-width="300">
          <template #default="{ row }">
            <button
              type="button"
              class="doc-name btn-press"
              :disabled="isBusy(row)"
              :aria-label="`查看 ${row.title} 的分块`"
              @click="openChunks(row)"
            >
              <el-icon class="doc-name__icon" aria-hidden="true"><Document /></el-icon>
              <span class="doc-name__text" :title="row.title">{{ row.title }}</span>
            </button>
          </template>
        </el-table-column>

        <!-- 状态：胶囊 + stage / 错误提示 + 重试 -->
        <el-table-column label="状态" width="130" align="center">
          <template #default="{ row }">
            <div class="doc-status-cell">
              <span class="doc-status" :class="`doc-status--${row.status}`">
                <span
                  v-if="row.status === 'running'"
                  class="doc-status__dot"
                  v-motion="breathMotion"
                  aria-hidden="true"
                ></span>
                <span>{{ statusLabel(row.status) }}</span>
                <span v-if="row.status === 'running' && row.stage" class="doc-status__stage">
                  · {{ stageLabel(row.stage) }}
                </span>
              </span>
              <el-tooltip
                v-if="row.status === 'failed' && row.error_message"
                :content="row.error_message"
                placement="top"
                :show-after="300"
              >
                <span class="doc-status__error" tabindex="0" aria-label="失败原因">
                  <el-icon aria-hidden="true"><Warning /></el-icon>
                </span>
              </el-tooltip>
              <button
                v-if="row.status === 'failed'"
                type="button"
                class="doc-action doc-action--primary btn-press"
                :disabled="reindexingId === row.id"
                @click="handleReindex(row)"
              >
                {{ reindexingId === row.id ? '重建中' : '重试' }}
              </button>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="Chunk 数" width="85" align="center">
          <template #default="{ row }">
            <span class="doc-num">{{ row.chunk_count }}</span>
          </template>
        </el-table-column>

        <el-table-column label="大小 (KB)" width="100" align="center">
          <template #default="{ row }">
            <span class="doc-num">{{ formatKb(row.file_size_bytes) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="上传时间" width="150" align="center">
          <template #default="{ row }">
            <span class="doc-num doc-time">{{ formatDateTime(row.created_at) }}</span>
          </template>
        </el-table-column>

        <!-- 操作列：查看 / 重建索引 / 删除；running 中禁用除删除外操作 -->
        <el-table-column label="操作" width="160" align="center">
          <template #default="{ row }">
            <div class="doc-actions">
              <button
                type="button"
                class="doc-action doc-action--view btn-press"
                :disabled="isBusy(row)"
                :aria-label="`查看 ${row.title} 原文与切片`"
                @click="openPreview(row)"
              >
                查看
              </button>
              <button
                type="button"
                class="doc-action doc-action--primary btn-press"
                :disabled="isBusy(row) || reindexingId === row.id"
                :aria-label="`重建 ${row.title} 的索引`"
                @click="confirmReindex(row)"
              >
                重建索引
              </button>
              <button
                type="button"
                class="doc-action doc-action--danger btn-press"
                :disabled="deletingId === row.id"
                :aria-label="`删除 ${row.title}`"
                @click="confirmDelete(row)"
              >
                {{ deletingId === row.id ? '删除中' : '删除' }}
              </button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页：15 / 30 / 100 每页，前端分页 -->
      <footer class="kb-docs__pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="filteredCount"
          :page-sizes="PAGE_SIZES"
          :pager-count="5"
          layout="total, sizes, prev, pager, next"
          background
        />
      </footer>
    </div>

    <!-- ══ 文档查看抽屉：原文 / 切片 双 Tab ══ -->
    <transition name="docs-drawer">
      <aside
        v-if="previewOpen"
        class="docs-drawer glass-strong"
        aria-label="文档查看"
      >
        <div class="docs-drawer__head">
          <div class="docs-drawer__title">
            <el-icon class="docs-drawer__title-icon" aria-hidden="true"><Document /></el-icon>
            <span class="docs-drawer__title-text" :title="previewTitle">{{ previewTitle || '文档' }}</span>
          </div>
          <button
            type="button"
            class="docs-drawer__close btn-press"
            aria-label="关闭文档查看"
            @click="closePreview"
          >
            <el-icon><Close /></el-icon>
          </button>
        </div>

        <div class="docs-drawer__tabs" role="tablist" aria-label="视图切换">
          <button
            type="button"
            role="tab"
            class="docs-drawer__tab btn-press"
            :class="{ 'docs-drawer__tab--active': previewTab === 'raw' }"
            :aria-selected="previewTab === 'raw'"
            @click="previewTab = 'raw'"
          >
            原文
          </button>
          <button
            type="button"
            role="tab"
            class="docs-drawer__tab btn-press"
            :class="{ 'docs-drawer__tab--active': previewTab === 'chunks' }"
            :aria-selected="previewTab === 'chunks'"
            @click="previewTab = 'chunks'"
          >
            切片
            <span v-if="previewDoc?.chunk_count" class="docs-drawer__tab-count kpi-num">{{ previewDoc.chunk_count }}</span>
          </button>
        </div>

        <div class="docs-drawer__body">
          <!-- 原文视图 -->
          <div v-if="previewTab === 'raw'" class="docs-drawer__raw">
            <div v-if="rawLoading" class="docs-drawer__state">
              <el-icon class="is-loading"><Loading /></el-icon>
              <p>加载原文…</p>
            </div>
            <div v-else-if="rawError" class="docs-drawer__state">
              <el-icon><Warning /></el-icon>
              <p>{{ rawError }}</p>
            </div>
            <pre v-else class="docs-drawer__raw-content">{{ rawContent }}</pre>
          </div>

          <!-- 切片视图：复用 chunk 列表 -->
          <div v-else class="docs-drawer__chunks">
            <div v-if="chunksLoading" class="docs-drawer__state">
              <el-icon class="is-loading"><Loading /></el-icon>
              <p>加载切片…</p>
            </div>
            <div v-else-if="!chunkItems.length" class="docs-drawer__state">
              <el-icon><Tickets /></el-icon>
              <p>暂无切片，文档解析完成后可见</p>
            </div>
            <div v-else class="docs-drawer__chunk-list">
              <article
                v-for="(chunk, i) in chunkItems"
                :key="chunk.chunk_id"
                class="docs-drawer__chunk glass-surface"
                v-motion="chunkMotion(i)"
              >
                <div class="docs-drawer__chunk-head">
                  <span class="docs-drawer__chunk-idx">#{{ i + 1 }}</span>
                  <span v-if="chunk.page != null" class="docs-drawer__chunk-page">第 {{ chunk.page }} 页</span>
                </div>
                <p class="docs-drawer__chunk-content">{{ chunk.content }}</p>
              </article>
              <div v-if="chunkTotal > chunkItems.length" class="docs-drawer__chunk-more">
                共 {{ chunkTotal }} 个切片，仅显示前 {{ chunkItems.length }} 个
              </div>
            </div>
          </div>
        </div>
      </aside>
    </transition>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Close, Document, Files, Loading, RefreshLeft, Search, Tickets, Upload, Warning } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteDoc,
  docStatus,
  getDocRaw,
  listDocs,
  normalizeDocStatus,
  reindexDoc,
  type DocDisplayStatus,
  type DocInfo,
  type DocStage,
} from '../api/docs'
import { listChunks } from '../api/retrieval'
import type { ChunkInfo } from '../api/retrieval'
import UploadDialog from '../components/UploadDialog.vue'
import JsonMappingWizard from '../components/json-mapping/JsonMappingWizard.vue'

const route = useRoute()
const router = useRouter()

/* ── 当前知识库 ID（来自 route.params.id） ── */
const kbId = computed(() => {
  const raw = Array.isArray(route.params.id) ? route.params.id[0] : route.params.id
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
})

/* ── 列表加载 ── */
const docs = ref<DocInfo[]>([])
const loading = ref(true)
const loadFailed = ref(false)
const errorMessage = ref('')

async function loadDocs() {
  const id = kbId.value
  if (id === null) {
    loadFailed.value = true
    errorMessage.value = '无效的知识库标识'
    return
  }
  loading.value = true
  loadFailed.value = false
  try {
    docs.value = await listDocs(id)
  } catch (err) {
    loadFailed.value = true
    errorMessage.value = getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(loadDocs)

/* ── 批量选择：多选 + 批量重建/删除 ── */
const tableRef = ref<{ clearSelection: () => void } | null>(null)
const selectedDocIds = ref<Set<number>>(new Set())
const batchBusy = ref(false)

function handleSelectionChange(rows: DocRow[]) {
  selectedDocIds.value = new Set(rows.map((r) => r.id))
}

function clearSelection() {
  selectedDocIds.value = new Set()
  tableRef.value?.clearSelection()
}

async function batchReindex() {
  const id = kbId.value
  if (id === null || selectedDocIds.value.size === 0 || batchBusy.value) return
  try {
    await ElMessageBox.confirm(
      `确定重建所选 ${selectedDocIds.value.size} 个文档的索引吗？将删除旧切片并重新解析入库。`,
      '批量重建索引',
      { type: 'warning', confirmButtonText: '重建', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batchBusy.value = true
  const ids = [...selectedDocIds.value]
  try {
    for (const docId of ids) {
      try {
        await reindexDoc(id, docId)
        docs.value = docs.value.map((d) =>
          d.id === docId ? { ...d, status: 'running', error_message: null } : d,
        )
        stageOverride.value = new Map(stageOverride.value).set(docId, null)
        pollDoc(docId)
      } catch (err) {
        ElMessage.error(`「${docId}」重建失败：${getErrorMessage(err)}`)
      }
    }
    ElMessage.success(`已提交 ${ids.length} 个文档的重建`)
    clearSelection()
  } finally {
    batchBusy.value = false
  }
}

async function batchDelete() {
  const id = kbId.value
  if (id === null || selectedDocIds.value.size === 0 || batchBusy.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除所选 ${selectedDocIds.value.size} 个文档吗？其分块与向量索引将一并删除，此操作不可恢复。`,
      '批量删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batchBusy.value = true
  const ids = [...selectedDocIds.value]
  try {
    // 并发删除（限 5 路）：逐个串行太慢，软删无冲突，可并行
    const CONCURRENCY = 5
    let failed = 0
    for (let i = 0; i < ids.length; i += CONCURRENCY) {
      const batch = ids.slice(i, i + CONCURRENCY)
      const results = await Promise.all(
        batch.map(async (docId) => {
          try {
            await deleteDoc(id, docId)
            return docId
          } catch {
            failed += 1
            return null
          }
        }),
      )
      // 成功项：停轮询 + 从列表移除（不可变更新）
      const okIds = results.filter((v): v is number => v !== null)
      if (okIds.length) {
        const okSet = new Set(okIds)
        for (const docId of okIds) {
          const timer = pollTimers.get(docId)
          if (timer) stopPoll(docId, timer)
        }
        docs.value = docs.value.filter((d) => !okSet.has(d.id))
        stageOverride.value = new Map(
          [...stageOverride.value.entries()].filter(([did]) => !okSet.has(did)),
        )
      }
    }
    if (failed === 0) {
      ElMessage.success(`已删除 ${ids.length} 个文档`)
    } else {
      ElMessage.error(`已删除 ${ids.length - failed} 个，失败 ${failed} 个`)
    }
    clearSelection()
  } finally {
    batchBusy.value = false
  }
}

/* ── 文档查看抽屉：原文 / 切片 双 Tab ── */
const previewOpen = ref(false)
const previewTab = ref<'raw' | 'chunks'>('raw')
const previewDoc = ref<{ id: number; title: string; chunk_count: number } | null>(null)
const rawContent = ref('')
const rawLoading = ref(false)
const rawError = ref('')
const chunkItems = ref<ChunkInfo[]>([])
const chunkTotal = ref(0)
const chunksLoading = ref(false)

const previewTitle = computed(() => previewDoc.value?.title ?? '')

async function openPreview(row: DocRow) {
  const id = kbId.value
  if (id === null) return
  previewDoc.value = { id: row.id, title: row.title, chunk_count: row.chunk_count }
  previewTab.value = 'raw'
  previewOpen.value = true
  await Promise.all([loadRaw(id, row.id), loadChunks(id, row.id)])
}

function closePreview() {
  previewOpen.value = false
  previewDoc.value = null
  rawContent.value = ''
  rawError.value = ''
  chunkItems.value = []
  chunkTotal.value = 0
}

async function loadRaw(id: number, docId: number) {
  rawLoading.value = true
  rawError.value = ''
  try {
    const raw = await getDocRaw(id, docId)
    rawContent.value = raw.content
  } catch (err) {
    rawError.value = getErrorMessage(err)
  } finally {
    rawLoading.value = false
  }
}

async function loadChunks(id: number, docId: number) {
  chunksLoading.value = true
  try {
    const page = await listChunks(id, docId, { page: 1, page_size: 50 })
    chunkItems.value = page.items
    chunkTotal.value = page.total
  } catch {
    chunkItems.value = []
    chunkTotal.value = 0
  } finally {
    chunksLoading.value = false
  }
}

function chunkMotion(i: number) {
  return {
    initial: { y: 8, opacity: 0 },
    enter: {
      y: 0,
      opacity: 1,
      delay: Math.min(i * 0.03, 0.3),
      transition: { type: 'spring', stiffness: 250, damping: 25 },
    },
  }
}

/* ── 表格行：列表数据 + 轮询注入的 stage 覆盖（不可变更新） ── */
interface DocRow {
  id: number
  title: string
  status: DocDisplayStatus
  stage: DocStage | null
  chunk_count: number
  file_size_bytes: number | null
  error_message: string | null
  created_at: string | null
}

const rows = computed<DocRow[]>(() =>
  docs.value.map((doc) => ({
    id: doc.id,
    title: doc.title,
    status: normalizeDocStatus(doc.status),
    stage: stageOverride.value.get(doc.id) ?? null,
    chunk_count: doc.chunk_count,
    file_size_bytes: doc.file_size_bytes,
    error_message: doc.error_message,
    created_at: doc.created_at,
  })),
)

/** 轮询状态对列表行的覆盖：docId -> stage（status/chunk_count 直接回写 docs） */
const stageOverride = ref<Map<number, DocStage | null>>(new Map())

/* ── 搜索（前端过滤：按文件名） ── */
const keyword = ref('')

/* ── 筛选条件（前端过滤，可组合） ── */
type StatusFilter = DocDisplayStatus | null
type SizeFilter = 'small' | 'medium' | 'large' | null
type TimeFilter = 'today' | '7d' | '30d' | null
type ChunkFilter = 'has' | 'none' | null

const STATUS_OPTIONS: { value: DocDisplayStatus; label: string }[] = [
  { value: 'pending', label: '等待中' },
  { value: 'running', label: '解析中' },
  { value: 'done', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'deleting', label: '删除中' },
]

const SIZE_OPTIONS: { value: Exclude<SizeFilter, null>; label: string }[] = [
  { value: 'small', label: '≤ 100 KB' },
  { value: 'medium', label: '100 KB - 1 MB' },
  { value: 'large', label: '≥ 1 MB' },
]

const TIME_OPTIONS: { value: Exclude<TimeFilter, null>; label: string }[] = [
  { value: 'today', label: '今天' },
  { value: '7d', label: '最近 7 天' },
  { value: '30d', label: '最近 30 天' },
]

const CHUNK_OPTIONS: { value: Exclude<ChunkFilter, null>; label: string }[] = [
  { value: 'has', label: '有切片' },
  { value: 'none', label: '无切片' },
]

const filterStatus = ref<StatusFilter>(null)
const filterSize = ref<SizeFilter>(null)
const filterTime = ref<TimeFilter>(null)
const filterChunks = ref<ChunkFilter>(null)

/** 是否有任一筛选条件生效（控制重置按钮可用态） */
const hasActiveFilters = computed(
  () =>
    keyword.value.trim() !== '' ||
    filterStatus.value !== null ||
    filterSize.value !== null ||
    filterTime.value !== null ||
    filterChunks.value !== null,
)

/** 先过筛选条件，再按文件名关键词过滤 */
const filteredDocs = computed<DocRow[]>(() => {
  const kw = keyword.value.trim().toLowerCase()
  const status = filterStatus.value
  const size = filterSize.value
  const time = filterTime.value
  const chunks = filterChunks.value
  const now = Date.now()
  return rows.value.filter((doc) => {
    if (status !== null && doc.status !== status) return false
    if (size !== null) {
      const bytes = doc.file_size_bytes ?? 0
      if (size === 'small' && !(bytes > 0 && bytes <= 100 * 1024)) return false
      if (size === 'medium' && !(bytes > 100 * 1024 && bytes < 1024 * 1024)) return false
      if (size === 'large' && !(bytes >= 1024 * 1024)) return false
    }
    if (time !== null) {
      if (!doc.created_at) return false
      const t = new Date(doc.created_at).getTime()
      if (Number.isNaN(t)) return false
      const dayMs = 24 * 60 * 60 * 1000
      if (time === 'today') {
        const d = new Date(t)
        const today = new Date()
        if (d.getFullYear() !== today.getFullYear() || d.getMonth() !== today.getMonth() || d.getDate() !== today.getDate()) {
          return false
        }
      } else if (now - t > (time === '7d' ? 7 : 30) * dayMs) {
        return false
      }
    }
    if (chunks !== null) {
      const c = doc.chunk_count ?? 0
      if (chunks === 'has' && !(c > 0)) return false
      if (chunks === 'none' && !(c === 0)) return false
    }
    if (kw && !doc.title.toLowerCase().includes(kw)) return false
    return true
  })
})

const filteredCount = computed(() => filteredDocs.value.length)

/* ── 分页（前端 slice，页码/每页条数变化即时更新） ── */
const PAGE_SIZES = [10, 30, 50, 100]
const pageSize = ref(10)
const page = ref(1)

function resetPage() {
  page.value = 1
}

/** 页码越界自动收拢（筛选后总数变少时兜底） */
watch(filteredCount, (n) => {
  if (page.value > 1) {
    const last = Math.max(1, Math.ceil(n / pageSize.value))
    if (page.value > last) page.value = last
  }
})

/** 每页条数变化：el-pagination 自动修正 current-page，这里显式回到第 1 页 */
watch(pageSize, () => {
  page.value = 1
})

const pagedDocs = computed<DocRow[]>(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredDocs.value.slice(start, start + pageSize.value)
})

/** 重置：清空全部筛选条件与关键词，回到第 1 页 */
function resetFilters() {
  keyword.value = ''
  filterStatus.value = null
  filterSize.value = null
  filterTime.value = null
  filterChunks.value = null
  page.value = 1
}

/* ── 进入分块页 ── */
function openChunks(row: DocRow) {
  if (isBusy(row)) return
  router.push({ name: 'kb-chunks', params: { id: String(kbId.value), docId: String(row.id) } })
}

/* ── 上传弹窗：UploadDialog 组件托管队列与并发，这里只接收成功回调 ── */
const uploadVisible = ref(false)

/** 上传成功：追加列表并启动轮询（UploadDialog 已构造 DocInfo） */
function handleUploaded(doc: DocInfo) {
  docs.value = [...docs.value, doc]
  pollDoc(doc.id)
}

/* ── JSON 映射向导 ── */
const wizardVisible = ref(false)
const wizardDocId = ref<number | null>(null)

/** JSON/JSONL 文件：关闭上传弹窗并打开映射向导（不进入 legacy 队列） */
function handleOpenJsonWizard(file: File) {
  // 单文件向导：一次只打开一个 JSON 会话
  void file
  uploadVisible.value = false
  wizardVisible.value = true
}

/** 向导确认入库成功：追加文档并轮询新状态（awaiting_mapping → ... → done） */
function handleWizardIngested(payload: { docId: number; jobId: number }) {
  wizardVisible.value = false
  wizardDocId.value = payload.docId
  const doc: DocInfo = {
    id: payload.docId,
    title: 'JSON 文档',
    source: 'JSON 映射',
    source_type: 'file',
    status: 'pending',
    chunk_count: 0,
    file_size_bytes: null,
    error_message: null,
    created_at: new Date().toISOString(),
    ingested_at: null,
  }
  docs.value = [...docs.value, doc]
  pollDoc(payload.docId)
}

/* ── 轮询 docStatus：2 秒间隔，直到 done/failed，卸载清理 ── */
const POLL_INTERVAL = 2000
const pollTimers = new Map<number, number>()
const polledDocs = ref<Set<number>>(new Set())

function pollDoc(docId: number) {
  if (polledDocs.value.has(docId)) return
  polledDocs.value = new Set(polledDocs.value).add(docId)
  const timer = window.setInterval(() => void tick(docId, timer), POLL_INTERVAL)
  pollTimers.set(docId, timer)
}

async function tick(docId: number, timer: number) {
  const id = kbId.value
  if (id === null) {
    stopPoll(docId, timer)
    return
  }
  try {
    const detail = await docStatus(id, docId)
    const status = normalizeDocStatus(detail.status)
    // 不可变更新：状态/chunk 数回写 docs，stage 走 stageOverride
    docs.value = docs.value.map((doc) =>
      doc.id === docId ? { ...doc, status: detail.status, chunk_count: detail.chunk_count } : doc,
    )
    stageOverride.value = new Map(stageOverride.value).set(docId, detail.stage)
    if (status === 'done' || status === 'failed') {
      stopPoll(docId, timer)
    }
  } catch {
    stopPoll(docId, timer)
  }
}

function stopPoll(docId: number, timer: number) {
  window.clearInterval(timer)
  pollTimers.delete(docId)
}

onBeforeUnmount(() => {
  pollTimers.forEach((timer) => window.clearInterval(timer))
  pollTimers.clear()
})

/* ── 重建索引：确认框 + reindexDoc + 轮询 ── */
const reindexingId = ref<number | null>(null)

async function confirmReindex(row: DocRow) {
  try {
    await ElMessageBox.confirm(
      `确定要重建「${row.title}」的索引吗？将删除原有分块并重新解析入库。`,
      '重建索引',
      {
        confirmButtonText: '重建',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    /* 用户取消，无操作 */
    return
  }
  await startReindex(row)
}

/** failed 状态行的快速重试（无二次确认） */
async function handleReindex(row: DocRow) {
  if (row.status !== 'failed' || reindexingId.value === row.id) return
  await startReindex(row)
}

async function startReindex(row: DocRow) {
  const id = kbId.value
  if (id === null || reindexingId.value !== null) return
  reindexingId.value = row.id
  try {
    await reindexDoc(id, row.id)
    // 重建后进入 running，轮询 stage 直到 done/failed
    docs.value = docs.value.map((doc) =>
      doc.id === row.id ? { ...doc, status: 'running', error_message: null } : doc,
    )
    stageOverride.value = new Map(stageOverride.value).set(row.id, null)
    ElMessage.success('已开始重建索引')
    pollDoc(row.id)
  } catch (err) {
    ElMessage.error(`重建失败：${getErrorMessage(err)}`)
  } finally {
    reindexingId.value = null
  }
}

/* ── 删除文档：确认框 + deleteDoc ── */
const deletingId = ref<number | null>(null)

async function confirmDelete(row: DocRow) {
  const id = kbId.value
  if (id === null || deletingId.value !== null) return
  try {
    await ElMessageBox.confirm(
      `确定要删除「${row.title}」吗？其分块与向量索引将一并删除，此操作不可恢复。`,
      '删除文档',
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
  deletingId.value = row.id
  try {
    await deleteDoc(id, row.id)
    // 停掉该文档的轮询，避免删除后 status 接口报错
    const timer = pollTimers.get(row.id)
    if (timer) stopPoll(row.id, timer)
    docs.value = docs.value.filter((doc) => doc.id !== row.id)
    stageOverride.value = new Map(
      [...stageOverride.value.entries()].filter(([docId]) => docId !== row.id),
    )
    ElMessage.success('文档已删除')
  } catch (err) {
    ElMessage.error(`删除失败：${getErrorMessage(err)}`)
  } finally {
    deletingId.value = null
  }
}

/* ── 行忙碌判定：running/deleting 中禁用除删除外操作 ── */
function isBusy(row: DocRow): boolean {
  return row.status === 'running' || row.status === 'deleting' || deletingId.value === row.id
}

/* ── 状态展示映射 ── */
const STATUS_LABEL: Record<DocDisplayStatus, string> = {
  pending: '等待中',
  running: '解析中',
  done: '已完成',
  failed: '失败',
  deleting: '删除中',
}

const STAGE_LABEL: Record<DocStage, string> = {
  parsing: '解析文本',
  chunking: '分块',
  embedding: '向量化',
  indexing: '写入索引',
}

function statusLabel(status: DocDisplayStatus): string {
  return STATUS_LABEL[status]
}

function stageLabel(stage: DocStage): string {
  return STAGE_LABEL[stage]
}

/* ── 格式化工具 ── */
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function formatKb(bytes: number | null): string {
  if (bytes === null || bytes === undefined || bytes < 0) return '—'
  return (bytes / 1024).toFixed(1)
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const message = (err as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请重试'
}

/* ── 动效：表格进入弹簧 + 呼吸点循环透明度（规范 4） ── */
const tableMotion = {
  initial: { y: 8, opacity: 0 },
  enter: { y: 0, opacity: 1, transition: { type: 'spring', stiffness: 250, damping: 25 } },
}

/* 上传面板进入：侧滑 + 淡入弹簧（规范 4.1 抽屉/面板阻尼） */
const panelMotion = {
  initial: { y: -12, opacity: 0 },
  enter: { y: 0, opacity: 1, transition: { type: 'spring', stiffness: 220, damping: 20 } },
}

/* 队列行进入：级联下移淡入 */
function itemMotion(i: number) {
  return {
    initial: { y: 6, opacity: 0 },
    enter: {
      y: 0,
      opacity: 1,
      delay: Math.min(i * 0.04, 0.4),
      transition: { type: 'spring', stiffness: 250, damping: 25 },
    },
  }
}

const breathMotion = {
  initial: { opacity: 0.35 },
  enter: {
    opacity: 1,
    transition: { type: 'tween', duration: 0.9, repeat: Infinity, repeatType: 'reverse', ease: 'easeInOut' },
  },
}
</script>

<style scoped>
.kb-docs {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

/* ── 顶部工具栏 ── */
.kb-docs__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.kb-docs__title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.kb-docs__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.kb-docs__search {
  width: 240px;
}

.kb-docs__upload {
  display: inline-flex;
}

.kb-docs__btn-icon {
  margin-right: 6px;
}

/* ── 表格卡片（规范 7 DataTable：透明底、细分隔线、行悬浮微亮） ── */
.kb-docs__table-card {
  padding: 8px 16px 16px;
  border-radius: var(--radius-3xl);
}

/* ── 筛选区：小控件并排 + 计数 + 重置（4px 网格间距） ── */
.kb-docs__filter {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 4px 12px;
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: 4px;
}

.kb-docs__filter-select {
  width: 132px;
}

.kb-docs__filter-name {
  width: 200px;
}

.kb-docs__filter-count {
  margin-left: auto;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.kb-docs__filter-reset {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent-blue);
  cursor: pointer;
  white-space: nowrap;
}

.kb-docs__filter-reset .el-icon {
  font-size: 13px;
}

.kb-docs__filter-reset:hover:not(:disabled) {
  color: var(--accent-indigo);
}

.kb-docs__filter-reset:disabled {
  color: var(--text-tertiary);
  cursor: not-allowed;
}

/* ── 分页 ── */
.kb-docs__pager {
  display: flex;
  justify-content: flex-end;
  padding-top: 14px;
}

.kb-docs__pager :deep(.el-pagination) {
  --el-pagination-bg-color: transparent;
  --el-pagination-button-bg-color: transparent;
  --el-pagination-hover-color: var(--accent-blue);
  --el-pagination-button-disabled-bg-color: transparent;
  --el-pagination-text-color: var(--text-secondary);
  font-family: inherit;
}

.kb-docs__pager :deep(.el-pagination.is-background .el-pager li) {
  min-width: 30px;
  height: 30px;
  border-radius: var(--radius-lg);
  font-size: 13px;
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.kb-docs__pager :deep(.el-pagination.is-background .el-pager li.is-active) {
  background: var(--accent-blue);
  color: #fff;
}

.kb-docs__pager :deep(.el-pagination.is-background .btn-prev),
.kb-docs__pager :deep(.el-pagination.is-background .btn-next) {
  min-width: 30px;
  height: 30px;
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.kb-docs__pager :deep(.el-pagination.is-background .btn-prev.is-disabled),
.kb-docs__pager :deep(.el-pagination.is-background .btn-next.is-disabled) {
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
  opacity: 0.45;
}

.kb-docs__pager :deep(.el-pagination__sizes .el-select .el-input .el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--border-subtle) inset;
  border-radius: var(--radius-lg);
}

/* ── 批量操作栏 ── */
/* ── 批量操作（并入筛选区） ── */
.kb-docs__batch-sep {
  width: 1px;
  height: 20px;
  background: var(--border-subtle);
  flex-shrink: 0;
}

.kb-docs__batch-count {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  flex-shrink: 0;
}

.kb-docs__filter .doc-action {
  font-size: 13px;
  flex-shrink: 0;
}


.kb-docs__table {
  width: 100%;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: transparent;
  --el-table-header-text-color: var(--text-secondary);
  --el-table-text-color: var(--text-primary);
  --el-table-border-color: var(--border-subtle);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--text-primary) 3%, transparent);
}

.kb-docs__table :deep(.el-table__inner-wrapper::before),
.kb-docs__table :deep(.el-table::before) {
  display: none;
}

.kb-docs__table :deep(th.el-table__cell) {
  background: transparent;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--text-secondary);
  text-align: center;
  border-bottom: 1px solid var(--border-subtle);
  padding: 12px 0;
}

.kb-docs__table :deep(td.el-table__cell) {
  background: transparent;
  border-bottom: 1px solid var(--border-subtle);
  padding: 12px 0;
}

/* ── 文件名（可点击） ── */
.doc-name {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}

.doc-name:disabled {
  cursor: default;
}

.doc-name:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
  border-radius: var(--radius-lg);
}

.doc-name__icon {
  font-size: 15px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.doc-name__text {
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── 状态列：flex 容器要居中得用 justify-content（text-align 对 flex 无效） ── */
.doc-status-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  max-width: 100%;
}

/* 状态胶囊：状态色 10-12% 透明底 + 同色文字，运行中 4px 呼吸点 */
.doc-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  white-space: nowrap;
}

.doc-status--pending {
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
}

.doc-status--running {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
}

.doc-status--done {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.doc-status--failed {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

.doc-status--deleting {
  background: color-mix(in srgb, var(--accent-orange) 12%, transparent);
  color: var(--accent-orange);
}

.doc-status__dot {
  width: 4px;
  height: 4px;
  border-radius: var(--radius-full);
  background: currentColor;
}

.doc-status__stage {
  font-weight: 500;
  opacity: 0.85;
}

/* 失败原因图标：悬停 tooltip 展示 error_message */
.doc-status__error {
  display: inline-flex;
  align-items: center;
  color: var(--accent-red);
  font-size: 14px;
  cursor: help;
}

/* ── 数值列 ── */
.doc-num {
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  color: var(--text-primary);
}

.doc-time {
  color: var(--text-secondary);
}

/* ── 操作列：text-xs 链接式按钮 ── */
.doc-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.doc-action {
  padding: 0;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.doc-action:disabled {
  color: var(--text-tertiary);
  cursor: not-allowed;
}

.doc-action--primary {
  color: var(--accent-blue);
}

.doc-action--primary:hover:not(:disabled) {
  color: var(--accent-indigo);
}

.doc-action--view {
  color: var(--accent-indigo);
}

.doc-action--view:hover:not(:disabled) {
  color: var(--accent-blue);
}

.doc-action--danger {
  color: var(--accent-red);
}

.doc-action--danger:hover:not(:disabled) {
  opacity: 0.8;
}

/* ── 页面级状态（加载/错误/空） ── */
.kb-docs__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.kb-docs__state-icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.kb-docs__state-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.kb-docs__state-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.kb-docs__state-btn {
  margin-top: 12px;
}

/* ═══════════ 文档查看抽屉 ═══════════ */
.docs-drawer {
  position: absolute;
  top: 24px;
  right: 24px;
  bottom: 24px;
  width: 480px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-3xl);
  padding: 20px;
  overflow: hidden;
}

.docs-drawer__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border-subtle);
}

.docs-drawer__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.docs-drawer__title-icon {
  font-size: 16px;
  color: var(--accent-indigo);
  flex-shrink: 0;
}

.docs-drawer__title-text {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.docs-drawer__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 15px;
  flex-shrink: 0;
}

.docs-drawer__close:hover {
  background: color-mix(in srgb, var(--text-primary) 6%, transparent);
  color: var(--text-primary);
}

/* ── Tab 切换 ── */
.docs-drawer__tabs {
  display: flex;
  gap: 4px;
  padding: 12px 0;
}

.docs-drawer__tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  border: none;
  border-radius: var(--radius-full);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.docs-drawer__tab--active {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
  font-weight: 600;
}

.docs-drawer__tab-count {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 15%, transparent);
  padding: 1px 8px;
  border-radius: var(--radius-full);
}

/* ── 内容区 ── */
.docs-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.docs-drawer__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 60px 0;
  color: var(--text-tertiary);
  font-size: 13px;
}

.docs-drawer__state .el-icon {
  font-size: 32px;
}

/* 原文：等宽字体，保留格式 */
.docs-drawer__raw-content {
  margin: 0;
  padding: 4px 2px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.8;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── 切片列表 ── */
.docs-drawer__chunk-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.docs-drawer__chunk {
  padding: 12px 14px;
  border-radius: var(--radius-2xl);
}

.docs-drawer__chunk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.docs-drawer__chunk-idx {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-indigo);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
}

.docs-drawer__chunk-page {
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.docs-drawer__chunk-content {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.docs-drawer__chunk-more {
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 0 4px;
}

/* 抽屉滑入动画 */
.docs-drawer-enter-active {
  transition: transform 0.25s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.2s ease;
}

.docs-drawer-leave-active {
  transition: transform 0.2s ease, opacity 0.15s ease;
}

.docs-drawer-enter-from,
.docs-drawer-leave-to {
  transform: translateX(24px);
  opacity: 0;
}

/* 抽屉滚动条 */
.docs-drawer__body::-webkit-scrollbar {
  width: 6px;
}

.docs-drawer__body::-webkit-scrollbar-track {
  background: transparent;
}

.docs-drawer__body::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-tertiary) 35%, transparent);
  border-radius: var(--radius-full);
}

.docs-drawer__body::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--text-tertiary) 60%, transparent);
}
</style>
