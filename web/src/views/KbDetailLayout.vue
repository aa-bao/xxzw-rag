<template>
  <div class="kb-detail">
    <!-- 左侧玻璃侧栏：返回 + 缩略图 + 名称/描述 + 菜单 + 删除 -->
    <aside class="kb-detail__sidebar glass-surface">
      <RouterLink to="/kb" class="kb-detail__back btn-press" aria-label="返回知识库列表">
        <el-icon class="kb-detail__back-icon"><ArrowLeft /></el-icon>
        <span>返回</span>
      </RouterLink>

      <template v-if="kb">
        <div class="kb-detail__head">
          <div
            class="kb-detail__thumb"
            :style="kb.cover_url ? undefined : { background: thumbGradient }"
            role="img"
            :aria-label="`${kb.name} 缩略图`"
          >
            <img
              v-if="kb.cover_url"
              :src="kb.cover_url"
              class="kb-detail__thumb-img"
              alt=""
            />
          </div>
          <h1 class="kb-detail__name">{{ kb.name }}</h1>
          <p class="kb-detail__desc">{{ kb.description || '暂无描述' }}</p>
          <p class="kb-detail__created">
            <el-icon class="kb-detail__created-icon" aria-hidden="true"><Calendar /></el-icon>
            <span class="kb-detail__created-text">创建于 {{ createdText }}</span>
          </p>
        </div>

        <nav class="kb-detail__nav" aria-label="知识库功能">
          <RouterLink
            v-for="item in navItems"
            :key="item.name"
            :to="{ name: item.name, params: { id: route.params.id } }"
            class="kb-detail__nav-item btn-press"
            :class="{ 'kb-detail__nav-item--active': isActive(item.name) }"
            :aria-current="isActive(item.name) ? 'page' : undefined"
          >
            <span class="kb-detail__nav-bar" aria-hidden="true"></span>
            <el-icon class="kb-detail__nav-icon" aria-hidden="true"><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </RouterLink>
        </nav>

        <button type="button" class="kb-detail__delete btn-press" @click="confirmDelete">
          <el-icon class="kb-detail__delete-icon" aria-hidden="true"><Delete /></el-icon>
          <span>删除知识库</span>
        </button>
      </template>
    </aside>

    <!-- 右侧内容区：嵌套子路由（文档 / 检索测试 / 配置） -->
    <main class="kb-detail__content">
      <!-- 加载中 -->
      <div v-if="loading" class="kb-detail__state">
        <el-icon class="kb-detail__state-icon is-loading"><Loading /></el-icon>
        <p class="kb-detail__state-hint">加载中…</p>
      </div>

      <!-- 加载失败（404 / 权限等） -->
      <div v-else-if="loadFailed" class="kb-detail__state">
        <el-icon class="kb-detail__state-icon"><Warning /></el-icon>
        <p class="kb-detail__state-title">知识库加载失败</p>
        <p class="kb-detail__state-hint">{{ errorMessage }}</p>
        <el-button type="primary" class="btn-press kb-detail__state-btn" @click="loadKb">重试</el-button>
      </div>

      <RouterView v-else :key="String(route.params.id)" v-motion="panelMotion" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  Calendar,
  DataAnalysis,
  Delete,
  Files,
  Loading,
  Setting,
  Warning,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteKb, getKb, type KbInfo } from '../api/kb'

const route = useRoute()
const router = useRouter()

const kb = ref<KbInfo | null>(null)
const loading = ref(true)
const loadFailed = ref(false)
const errorMessage = ref('')

/* ── 菜单项：RouterLink 到嵌套子路由 ── */
const navItems = [
  { name: 'kb-docs', label: '文档', icon: Files },
  { name: 'kb-testing', label: '检索测试', icon: DataAnalysis },
  { name: 'kb-config', label: '配置', icon: Setting },
]

function isActive(name: string): boolean {
  return route.name === name
}

/* ── 按 route.params.id 加载 KB（404 / 权限失败显示错误态） ── */
const kbId = computed(() => {
  const raw = Array.isArray(route.params.id) ? route.params.id[0] : route.params.id
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : null
})

async function loadKb() {
  const id = kbId.value
  if (id === null) {
    loadFailed.value = true
    errorMessage.value = '无效的知识库标识'
    return
  }
  loading.value = true
  loadFailed.value = false
  kb.value = null
  try {
    kb.value = await getKb(id)
  } catch (err) {
    loadFailed.value = true
    errorMessage.value = getErrorMessage(err)
  } finally {
    loading.value = false
  }
}

onMounted(loadKb)
watch(kbId, loadKb)

/* ── 缩略图：按 kb.id 确定性渐变（仅引用令牌，与列表页同风格） ── */
const THUMB_GRADIENTS = [
  'linear-gradient(135deg, var(--accent-blue), var(--accent-indigo))',
  'linear-gradient(135deg, var(--accent-indigo), var(--accent-orange))',
  'linear-gradient(135deg, var(--accent-green), var(--accent-blue))',
  'linear-gradient(135deg, var(--accent-orange), var(--accent-indigo))',
] as const

const thumbGradient = computed(() => {
  const len = THUMB_GRADIENTS.length
  const id = kb.value?.id ?? 0
  return THUMB_GRADIENTS[((id % len) + len) % len]
})

/* ── 删除知识库：确认后调 deleteKb，成功后跳回列表 ── */
async function confirmDelete() {
  if (!kb.value) return
  try {
    await ElMessageBox.confirm(
      `确定要删除知识库「${kb.value.name}」吗？其下所有文档与索引将被一并删除，此操作不可恢复。`,
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
    await deleteKb(kb.value.id)
    ElMessage.success('知识库已删除')
    router.push('/kb')
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
  return '加载失败，请重试'
}

/* ── 格式化 ── */
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

const createdText = computed(() => {
  const iso = kb.value?.created_at
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
})

/* ── 动效：内容区弹簧进入（规范 4.1） ── */
const panelMotion = {
  initial: { y: 8, opacity: 0 },
  enter: { y: 0, opacity: 1, transition: { type: 'spring', stiffness: 250, damping: 25 } },
}
</script>

<style scoped>
.kb-detail {
  display: flex;
  height: 100%;
  min-height: 0;
}

/* ═══════════ 侧栏 ═══════════ */
.kb-detail__sidebar {
  width: 288px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 16px 16px 20px;
  border-right: 1px solid var(--border-subtle);
  border-radius: 0;
  overflow-y: auto;
}

.kb-detail__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 6px 10px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
  margin-bottom: 16px;
}

.kb-detail__back:hover {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.kb-detail__back-icon {
  font-size: 16px;
}

.kb-detail__head {
  display: flex;
  flex-direction: column;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border-subtle);
}

.kb-detail__thumb {
  width: 70px;
  height: 70px;
  border-radius: var(--radius-2xl);
  margin-bottom: 14px;
  box-shadow: var(--shadow-card);
  overflow: hidden;
}

.kb-detail__thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  display: block;
}

.kb-detail__name {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.3;
  color: var(--text-primary);
  margin-bottom: 4px;
  overflow-wrap: break-word;
}

.kb-detail__desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-bottom: 10px;
}

.kb-detail__created {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
}

.kb-detail__created-icon {
  font-size: 14px;
}

.kb-detail__created-text {
  font-variant-numeric: tabular-nums;
}

/* ── 菜单：激活项左侧 5px 发光竖条 + 蓝 12% 底（规范 7） ── */
.kb-detail__nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 20px;
  flex: 1;
}

.kb-detail__nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  text-decoration: none;
}

.kb-detail__nav-item:hover {
  color: var(--text-primary);
}

.kb-detail__nav-item--active {
  background: color-mix(in srgb, var(--accent-blue) 12%, transparent);
  color: var(--accent-blue);
  font-weight: 600;
}

.kb-detail__nav-bar {
  width: 5px;
  height: 16px;
  flex-shrink: 0;
  border-radius: var(--radius-full);
  background: transparent;
}

.kb-detail__nav-item--active .kb-detail__nav-bar {
  background: var(--accent-blue);
}

.kb-detail__nav-icon {
  font-size: 16px;
}

/* ── 删除按钮：outline 红色 ── */
.kb-detail__delete {
  margin-top: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--accent-red);
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--accent-red);
  font-family: inherit;
  font-size: 14px;
  cursor: pointer;
}

.kb-detail__delete:hover {
  background: color-mix(in srgb, var(--accent-red) 8%, transparent);
}

.kb-detail__delete-icon {
  font-size: 16px;
}

/* ═══════════ 内容区 ═══════════ */
.kb-detail__content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 24px;
}

/* ── 加载 / 错误态 ── */
.kb-detail__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 72px 0;
}

.kb-detail__state-icon {
  font-size: 48px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.kb-detail__state-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.kb-detail__state-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.kb-detail__state-btn {
  margin-top: 12px;
}
</style>
