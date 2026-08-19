<!-- 系统设置：整个系统的运行状态与环境信息 -->
<template>
  <div class="page">
    <header class="page__header">
      <h1 class="page__title">系统设置</h1>
      <span class="page__subtitle">整个系统的运行状态、版本与环境信息</span>
    </header>

    <!-- 系统状态 -->
    <section class="card glass-surface" v-motion="cardMotion(0)" aria-label="系统状态">
      <h2 class="card__title">系统状态</h2>
      <div class="status-row">
        <span class="status-row__label">后端服务</span>
        <span class="status-pill" :class="health === 'online' ? 'status-pill--online' : 'status-pill--offline'">
          <span
            class="status-pill__dot"
            :class="{ 'status-pill__dot--live': health === 'online' }"
            v-motion="breathMotion"
            aria-hidden="true"
          ></span>
          <span>{{ healthLabel }}</span>
        </span>
      </div>
      <p class="card__hint status-hint">每 30 秒自动检测一次，亦可在下方手动刷新。</p>
      <button type="button" class="refresh-btn btn-press" aria-label="重新检测后端状态" @click="checkHealth">
        <el-icon class="refresh-btn__icon" aria-hidden="true"><Refresh /></el-icon>
        <span>重新检测</span>
      </button>
    </section>

    <!-- 版本与环境 -->
    <section class="card glass-surface" v-motion="cardMotion(1)" aria-label="版本与环境">
      <h2 class="card__title">版本与环境</h2>
      <dl class="info-list">
        <div class="info-row"><dt>应用标识</dt><dd>{{ version.appKey || '—' }}</dd></div>
        <div class="info-row"><dt>运行环境</dt><dd>{{ environmentLabel }}</dd></div>
        <div class="info-row"><dt>版本号</dt><dd>{{ version.version || '—' }}</dd></div>
        <div class="info-row"><dt>源码提交</dt><dd class="info-row__mono">{{ version.sourceCommit || '—' }}</dd></div>
        <div class="info-row"><dt>配置版本</dt><dd>{{ version.configVersion || '—' }}</dd></div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { applicationUrl } from '../platform'

type Health = 'unknown' | 'online' | 'offline'

interface ReleaseInfo {
  appKey?: string
  environment?: string
  version?: string
  sourceCommit?: string
  configVersion?: string
}

const health = ref<Health>('unknown')
let healthTimer: number | undefined

const version = ref<ReleaseInfo>({})

const healthLabel = computed(() => {
  switch (health.value) {
    case 'online':
      return '在线'
    case 'offline':
      return '离线'
    default:
      return '检测中'
  }
})

const environmentLabel = computed(() => {
  const env = version.value.environment
  if (!env) return '—'
  const map: Record<string, string> = {
    LOCAL: '本地开发',
    CONFORMANCE: '符合性测试',
    DEV: '开发环境',
    TEST: '测试环境',
    PROD: '生产环境',
  }
  return map[env] ?? env
})

async function checkHealth() {
  try {
    const resp = await fetch(applicationUrl('api/health/live'), { credentials: 'same-origin' })
    if (resp.ok) {
      const body = (await resp.json()) as { success?: boolean; data?: { status?: string } }
      health.value = body.success && body.data?.status === 'live' ? 'online' : 'offline'
    } else {
      health.value = 'offline'
    }
  } catch {
    health.value = 'offline'
  }
}

async function loadVersion() {
  try {
    const resp = await fetch(applicationUrl('platform/version'), { credentials: 'same-origin' })
    if (resp.ok) {
      const body = (await resp.json()) as ReleaseInfo
      version.value = body
    }
  } catch {
    // 版本信息加载失败不影响页面：保留占位符
  }
}

onMounted(() => {
  checkHealth()
  loadVersion()
  healthTimer = window.setInterval(checkHealth, 30000)
})

onUnmounted(() => {
  if (healthTimer !== undefined) window.clearInterval(healthTimer)
})

/* ── 动效：弹簧错峰进入（规范 4.1） ── */
function cardMotion(i: number) {
  return {
    initial: { y: 8, opacity: 0 },
    enter: {
      y: 0,
      opacity: 1,
      delay: Math.min(i * 60, 240),
      transition: { type: 'spring', stiffness: 250, damping: 25 },
    },
  }
}

/* ── 在线状态点呼吸脉冲：v-motion 循环透明度（禁 CSS keyframes） ── */
const breathMotion = {
  initial: { opacity: 0.35 },
  enter: {
    opacity: 1,
    transition: { type: 'tween', duration: 0.9, repeat: Infinity, repeatType: 'reverse', ease: 'easeInOut' },
  },
}
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

/* ── 玻璃卡片 ── */
.card {
  width: 100%;
  padding: 20px;
  border-radius: var(--radius-3xl);
  margin-bottom: 20px;
}

.card__title {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.card__hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
  margin-bottom: 20px;
}

/* ── 状态胶囊 ── */
.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0 12px;
}

.status-row__label {
  font-size: 13px;
  color: var(--text-primary);
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

.status-pill--online {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.status-pill--offline {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

.status-pill__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: currentColor;
}

.status-hint {
  margin-bottom: 12px;
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  border: none;
  background: transparent;
  color: var(--accent-blue);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.refresh-btn:hover {
  color: var(--accent-indigo);
}

.refresh-btn__icon {
  font-size: 14px;
}

/* ── 信息列表 ── */
.info-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 4px 0 8px;
}

.info-row {
  display: flex;
  align-items: baseline;
  gap: 16px;
  font-size: 13.5px;
}

.info-row dt {
  width: 120px;
  flex-shrink: 0;
  color: var(--text-tertiary);
  font-size: 13px;
}

.info-row dd {
  margin: 0;
  color: var(--text-primary);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12.5px;
  word-break: break-all;
}
</style>
