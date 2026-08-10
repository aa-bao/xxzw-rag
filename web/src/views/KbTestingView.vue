<template>
  <section class="kb-testing" aria-label="检索测试">
    <!-- 顶部标题栏 -->
    <header class="kb-testing__bar">
      <h1 class="kb-testing__title">检索测试</h1>
      <span v-if="!kbLoading && kb" class="kb-testing__kb-name">{{ kb.name }}</span>
    </header>

    <div class="kb-testing__layout">
      <!-- 左栏：控制面板 -->
      <aside class="kb-testing__panel glass-surface" aria-label="检索参数">
        <div class="panel-field">
          <label class="panel-label" for="kb-testing-threshold">相似度阈值</label>
          <el-slider
            id="kb-testing-threshold"
            v-model="threshold"
            :min="0"
            :max="1"
            :step="0.05"
            :show-tooltip="false"
            :disabled="kbLoading"
          />
          <p class="panel-value">{{ threshold.toFixed(2) }}</p>
        </div>

        <div class="panel-field">
          <label class="panel-label" for="kb-testing-topk">Top K</label>
          <el-input-number
            id="kb-testing-topk"
            v-model="topK"
            :min="1"
            :max="20"
            :step="1"
            controls-position="right"
            :disabled="kbLoading"
          />
        </div>

        <div class="panel-field">
          <label class="panel-label" for="kb-testing-question">问题</label>
          <el-input
            id="kb-testing-question"
            v-model="question"
            type="textarea"
            :rows="6"
            placeholder="输入要检索的问题…"
            :disabled="searching"
          />
        </div>

        <el-button
          type="primary"
          class="btn-press kb-testing__btn"
          :loading="searching"
          :disabled="!question.trim()"
          @click="handleSearch"
        >
          <el-icon class="kb-testing__btn-icon" aria-hidden="true"><Search /></el-icon>
          检索
        </el-button>
        <p class="panel-hint">
          按 Top K 召回与问题最相关的片段，仅保留相似度不低于阈值的分块
        </p>
      </aside>

      <!-- 右栏：结果区 -->
      <div class="kb-testing__results">
        <div class="kb-testing__results-head">
          <h2 class="kb-testing__results-title">检索结果</h2>
          <span v-if="searched" class="kb-testing__count kpi-num">{{ sortedResults.length }}</span>
        </div>

        <div v-if="!searched" class="kb-testing__empty">
          <el-icon class="kb-testing__empty-icon" aria-hidden="true"><Search /></el-icon>
          <p class="kb-testing__empty-title">输入问题开始检索</p>
          <p class="kb-testing__empty-hint">在左侧输入问题，点击「检索」查看召回结果</p>
        </div>

        <div v-else-if="!sortedResults.length" class="kb-testing__empty">
          <el-icon class="kb-testing__empty-icon" aria-hidden="true"><DocumentRemove /></el-icon>
          <p class="kb-testing__empty-title">未检索到相关内容</p>
          <p class="kb-testing__empty-hint">试试降低相似度阈值，或换个问题</p>
        </div>

        <div v-else class="kb-testing__list">
          <article
            v-for="(item, i) in sortedResults"
            :key="item.chunk_id"
            class="result-card glass-surface"
            v-motion="resultMotion(i)"
            :hovered="hoverMotion"
          >
            <div class="result-card__head">
              <span class="result-card__title">
                <el-icon class="result-card__title-icon" aria-hidden="true"><Document /></el-icon>
                <span class="result-card__title-text">{{ item.title }}</span>
              </span>
              <span class="result-card__score" :title="`相似度 ${scorePercent(item.score)}`">
                {{ scorePercent(item.score) }}
              </span>
            </div>
            <p v-if="item.page !== null" class="result-card__meta">
              <el-icon class="result-card__meta-icon" aria-hidden="true"><Collection /></el-icon>
              <span>第 {{ item.page }} 页</span>
            </p>
            <p class="result-card__snippet">{{ item.content }}</p>
          </article>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Collection, Document, DocumentRemove, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getKb, type KbInfo } from '../api/kb'
import { testRetrieval, type RetrievedChunk } from '../api/retrieval'

const route = useRoute()

/* ── 当前知识库 ID（来自 route.params.id） ── */
const kbId = computed(() => {
  const raw = Array.isArray(route.params.id) ? route.params.id[0] : route.params.id
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
})

/* ── 默认参数：优先取 KB 配置（getKb），失败回退默认值 ── */
const kb = ref<KbInfo | null>(null)
const kbLoading = ref(true)
const threshold = ref(0.5)
const topK = ref(8)

async function loadKb() {
  const id = kbId.value
  if (id === null) {
    kbLoading.value = false
    return
  }
  kbLoading.value = true
  try {
    kb.value = await getKb(id)
    threshold.value = kb.value.similarity_threshold
    topK.value = Math.max(1, Math.min(20, kb.value.top_k))
  } catch {
    // 获取配置失败时保留默认参数，检索本身仍可用
    kb.value = null
  } finally {
    kbLoading.value = false
  }
}

onMounted(loadKb)

/* ── 检索 ── */
const question = ref('')
const searching = ref(false)
const results = ref<RetrievedChunk[]>([])
const searched = ref(false)

async function handleSearch() {
  const id = kbId.value
  const q = question.value.trim()
  if (id === null) {
    ElMessage.error('无效的知识库标识')
    return
  }
  if (!q) {
    ElMessage.warning('请输入要检索的问题')
    return
  }
  searching.value = true
  try {
    results.value = await testRetrieval(id, {
      question: q,
      top_k: topK.value,
      similarity_threshold: threshold.value,
    })
    searched.value = true
  } catch (err) {
    searched.value = false
    ElMessage.error(getErrorMessage(err))
  } finally {
    searching.value = false
  }
}

/* ── 相似度百分比：score（余弦相似度，范围 [-1, 1]）× 100 保留 1 位小数；
   负分=无关，如实显示负百分比，不隐藏不归零 ── */
function scorePercent(score: number): string {
  return `${(score * 100).toFixed(1)}%`
}

/* ── 结果按相似度降序（cosine 空间下 score 高=更相似） ── */
const sortedResults = computed(() =>
  [...results.value].sort((a, b) => b.score - a.score),
)

/* ── 错误信息提取（api client 抛出的响应体） ── */
function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const message = (err as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '检索失败，请重试'
}

/* ── 动效：结果卡片弹簧错峰进入（规范 4.1），悬停上浮由 :hovered 接管 ── */
const HOVER_SPRING = { type: 'spring', stiffness: 440, damping: 42 } as const

function resultMotion(i: number) {
  return {
    initial: { y: 8, opacity: 0 },
    enter: {
      y: 0,
      opacity: 1,
      delay: Math.min(i * 40, 320),
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
.kb-testing {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

/* ── 顶部标题栏 ── */
.kb-testing__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.kb-testing__title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.kb-testing__kb-name {
  padding: 3px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 左右分栏（参考旧 KbDetailView testing 区域） ── */
.kb-testing__layout {
  display: flex;
  align-items: flex-start;
  gap: 24px;
  flex: 1;
  min-height: 0;
}

/* ── 左栏：控制面板 ── */
.kb-testing__panel {
  width: 340px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 20px;
  padding: 20px;
  border-radius: var(--radius-3xl);
}

.panel-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.panel-label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: var(--text-secondary);
}

.panel-value {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  text-align: right;
  margin-top: -4px;
}

.kb-testing__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.kb-testing__btn-icon {
  margin-right: 6px;
}

.panel-hint {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
}

/* ── 右栏：结果区 ── */
.kb-testing__results {
  flex: 1;
  min-width: 0;
}

.kb-testing__results-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.kb-testing__results-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.kb-testing__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 7px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

/* ── 空状态 ── */
.kb-testing__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.kb-testing__empty-icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.kb-testing__empty-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.kb-testing__empty-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

/* ── 结果列表 ── */
.kb-testing__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-card {
  padding: 16px 20px;
  border-radius: var(--radius-3xl);
}

.result-card:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

.result-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.result-card__title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.result-card__title-icon {
  font-size: 15px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.result-card__title-text {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 相似度徽章：蓝色调（分数越高越好） */
.result-card__score {
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

/* 来源：页码 */
.result-card__meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.result-card__meta-icon {
  font-size: 12px;
}

.result-card__snippet {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  word-break: break-word;
  white-space: pre-wrap;
}
</style>
