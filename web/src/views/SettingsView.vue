<template>
  <div class="page">
    <header class="page__header">
      <h1 class="page__title">设置</h1>
    </header>

    <!-- 模型配置（可视化表单） -->
    <section class="card glass-surface" v-motion="cardMotion(0)" aria-label="模型配置">
      <div class="card__head">
        <h2 class="card__title">模型配置</h2>
        <el-button
          v-if="dirty"
          text
          size="small"
          class="card__reset btn-press"
          aria-label="放弃修改"
          @click="resetForm"
        >
          放弃修改
        </el-button>
      </div>
      <p class="card__hint">
        配置 Chat 与 Embedding 各一个模型。保存后立即生效，无需重启服务；新建知识库将使用新的 Embedding 模型。
      </p>

      <!-- Chat 模型 -->
      <div class="model-block">
        <div class="model-block__head">
          <span class="model-block__title">Chat 模型</span>
          <el-icon class="model-block__icon" aria-hidden="true"><ChatDotRound /></el-icon>
        </div>
        <div class="model-block__body">
          <el-form :model="chatForm" label-position="top" class="model-form">
            <el-form-item label="Base URL">
              <el-input v-model="chatForm.base_url" placeholder="https://api.example.com/v1" clearable />
            </el-form-item>
            <el-form-item label="模型名">
              <el-input v-model="chatForm.chat_model" placeholder="gpt-4o / deepseek-v4-flash" clearable />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input
                v-model="chatForm.api_key"
                type="password"
                show-password
                :placeholder="hasChatKey ? '已配置（留空则不变）' : '请输入 API Key'"
                autocomplete="new-password"
              />
            </el-form-item>
          </el-form>
          <div class="model-block__test">
            <span class="model-block__result" :class="chatTestResult ? (chatTestResult.ok ? 'model-block__result--ok' : 'model-block__result--fail') : ''">
              <el-icon v-if="chatTestResult" aria-hidden="true">
                <component :is="chatTestResult.ok ? CircleCheck : CircleClose" />
              </el-icon>
              <span v-if="chatTestResult">{{ chatTestResult.ok ? '连接成功' : (chatTestResult.message || '连接失败') }}</span>
            </span>
            <el-button
              class="btn-press"
              :loading="chatTesting"
              :disabled="!canTestChat"
              @click="handleTestChat"
            >
              <el-icon class="card__btn-icon" aria-hidden="true"><Connection /></el-icon>
              测试 Chat
            </el-button>
          </div>
        </div>
      </div>

      <!-- Embedding 模型 -->
      <div class="model-block">
        <div class="model-block__head">
          <span class="model-block__title">Embedding 模型</span>
          <el-icon class="model-block__icon" aria-hidden="true"><DataLine /></el-icon>
        </div>
        <div class="model-block__body">
          <el-form :model="embedForm" label-position="top" class="model-form">
            <el-form-item label="Base URL">
              <el-input v-model="embedForm.embedding_base_url" placeholder="留空则与 Chat 共用" clearable />
            </el-form-item>
            <el-form-item label="模型名">
              <el-input v-model="embedForm.embedding_model" placeholder="BAAI/bge-large-zh-v1.5" clearable />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input
                v-model="embedForm.embedding_api_key"
                type="password"
                show-password
                :placeholder="hasEmbedKey ? '已配置（留空则不变）' : '留空则与 Chat 共用 API Key'"
                autocomplete="new-password"
              />
            </el-form-item>
          </el-form>
          <div class="model-block__test">
            <span class="model-block__result" :class="embedTestResult ? (embedTestResult.ok ? 'model-block__result--ok' : 'model-block__result--fail') : ''">
              <el-icon v-if="embedTestResult" aria-hidden="true">
                <component :is="embedTestResult.ok ? CircleCheck : CircleClose" />
              </el-icon>
              <span v-if="embedTestResult">{{ embedTestResult.ok ? `连接成功，向量维度 ${embedTestResult.dimension}` : (embedTestResult.message || '连接失败') }}</span>
            </span>
            <el-button
              class="btn-press"
              :loading="embedTesting"
              :disabled="!canTestEmbed"
              @click="handleTestEmbed"
            >
              <el-icon class="card__btn-icon" aria-hidden="true"><Connection /></el-icon>
              测试 Embedding
            </el-button>
          </div>
        </div>
      </div>

      <div class="card__actions">
        <el-button
          type="primary"
          class="btn-press"
          :loading="saving"
          :disabled="chatTesting || embedTesting || !canSave"
          @click="handleSave"
        >
          保存配置
        </el-button>
      </div>
    </section>

    <!-- 系统状态 -->
    <section class="card glass-surface" v-motion="cardMotion(1)" aria-label="系统状态">
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ChatDotRound, CircleCheck, CircleClose, Connection, DataLine, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  getModelSettings,
  testModelSettings,
  updateModelSettings,
  type ModelSettings,
} from '../api/settings'

type Health = 'unknown' | 'online' | 'offline'

const health = ref<Health>('unknown')
let healthTimer: number | undefined
let loadToken = 0

/* ── 模型表单 ── */
const loaded = ref<ModelSettings | null>(null)
const loading = ref(true)
const saving = ref(false)
const chatTesting = ref(false)
const embedTesting = ref(false)
const chatTestResult = ref<{ ok: boolean; message?: string } | null>(null)
const embedTestResult = ref<{ ok: boolean; message?: string; dimension?: number } | null>(null)

const chatForm = reactive({
  base_url: '',
  chat_model: '',
  api_key: '',
})
const embedForm = reactive({
  embedding_base_url: '',
  embedding_model: '',
  embedding_api_key: '',
})

const hasChatKey = computed(() => loaded.value?.has_chat_api_key ?? false)
const hasEmbedKey = computed(() => loaded.value?.has_embedding_api_key ?? false)

const dirty = computed(() => {
  if (!loaded.value) return false
  const embedUrl = embedForm.embedding_base_url.trim() || null
  return (
    chatForm.base_url !== loaded.value.base_url ||
    chatForm.chat_model !== loaded.value.chat_model ||
    chatForm.api_key !== '' ||
    embedForm.embedding_model !== loaded.value.embedding_model ||
    embedUrl !== (loaded.value.embedding_base_url || null) ||
    embedForm.embedding_api_key !== ''
  )
})

const canSave = computed(() => {
  if (loading.value || saving.value) return false
  const embedUrl = embedForm.embedding_base_url.trim()
  return (
    chatForm.base_url.trim() !== '' &&
    chatForm.chat_model.trim() !== '' &&
    embedForm.embedding_model.trim() !== '' &&
    (embedUrl === '' || embedUrl.startsWith('http'))
  )
})

const canTestChat = computed(() => {
  if (loading.value || chatTesting.value) return false
  return chatForm.base_url.trim() !== '' && chatForm.chat_model.trim() !== ''
})

const canTestEmbed = computed(() => {
  if (loading.value || embedTesting.value) return false
  return (
    chatForm.base_url.trim() !== '' &&
    embedForm.embedding_model.trim() !== '' &&
    (embedForm.embedding_base_url.trim() === '' || embedForm.embedding_base_url.trim().startsWith('http'))
  )
})

function applySettings(s: ModelSettings) {
  chatForm.base_url = s.base_url
  chatForm.chat_model = s.chat_model
  chatForm.api_key = ''
  embedForm.embedding_base_url = s.embedding_base_url ?? ''
  embedForm.embedding_model = s.embedding_model
  embedForm.embedding_api_key = ''
}

async function loadModels() {
  const token = ++loadToken
  try {
    const s = await getModelSettings()
    if (token !== loadToken) return
    loaded.value = s
    applySettings(s)
  } catch (err) {
    if (token !== loadToken) return
    ElMessage.error('模型配置加载失败')
  } finally {
    if (token === loadToken) loading.value = false
  }
}

function resetForm() {
  if (!loaded.value) return
  applySettings(loaded.value)
  chatTestResult.value = null
  embedTestResult.value = null
}

function collectPayload() {
  return {
    base_url: chatForm.base_url.trim(),
    chat_model: chatForm.chat_model.trim(),
    embedding_model: embedForm.embedding_model.trim(),
    // 空串 = 与 chat 共用
    embedding_base_url: embedForm.embedding_base_url.trim() || null,
    // 空串 = 不更新
    api_key: chatForm.api_key,
    embedding_api_key: embedForm.embedding_api_key,
  }
}

async function handleSave() {
  if (!canSave.value) return
  saving.value = true
  try {
    const s = await updateModelSettings(collectPayload())
    loaded.value = s
    applySettings(s)
    chatTestResult.value = null
    embedTestResult.value = null
    ElMessage.success('模型配置已保存，立即生效')
  } catch (err) {
    const message = (err as { error?: { message?: string } })?.error?.message
    ElMessage.error(message || '保存失败，请重试')
  } finally {
    saving.value = false
  }
}

async function handleTestChat() {
  if (!canTestChat.value) return
  chatTesting.value = true
  chatTestResult.value = null
  try {
    const r = await testModelSettings({
      mode: 'chat',
      base_url: chatForm.base_url.trim(),
      chat_model: chatForm.chat_model.trim(),
      api_key: chatForm.api_key || undefined,
    })
    chatTestResult.value = r.ok ? { ok: true } : { ok: false, message: r.message }
  } catch {
    chatTestResult.value = { ok: false, message: '测试失败：无法连接后端' }
  } finally {
    chatTesting.value = false
  }
}

async function handleTestEmbed() {
  if (!canTestEmbed.value) return
  embedTesting.value = true
  embedTestResult.value = null
  try {
    const r = await testModelSettings({
      mode: 'embedding',
      base_url: chatForm.base_url.trim(),
      embedding_base_url: embedForm.embedding_base_url.trim() || null,
      embedding_model: embedForm.embedding_model.trim(),
      embedding_api_key: embedForm.embedding_api_key || undefined,
    })
    embedTestResult.value = r.ok
      ? { ok: true, dimension: (r as { dimension?: number }).dimension }
      : { ok: false, message: r.message }
  } catch {
    embedTestResult.value = { ok: false, message: '测试失败：无法连接后端' }
  } finally {
    embedTesting.value = false
  }
}

/* ── 健康检查 ── */
async function checkHealth() {
  try {
    const resp = await fetch('/api/health/live', { credentials: 'same-origin' })
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

onMounted(() => {
  loadModels()
  checkHealth()
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

/* ── 玻璃卡片 ── */
.card {
  width: 100%;
  padding: 20px;
  border-radius: var(--radius-3xl);
  margin-bottom: 20px;
}

.card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.card__title {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.card__head .card__title {
  margin-bottom: 0;
}

.card__reset {
  color: var(--text-secondary);
}

.card__hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
  margin-bottom: 20px;
}

/* ── 模型区块 ── */
.model-block {
  margin-bottom: 24px;
}

.model-block:last-of-type {
  margin-bottom: 16px;
}

.model-block__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.model-block__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.model-block__icon {
  font-size: 15px;
  color: var(--accent-indigo);
}

.model-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.model-form :deep(.el-form-item) {
  margin-bottom: 0;
}

.model-form :deep(.el-form-item__label) {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ── 模型块：输入框 + 右侧测试按钮 ── */
.model-block__body {
  display: flex;
  align-items: flex-end;
  gap: 20px;
}

.model-block__body .model-form {
  flex: 1;
  min-width: 0;
}

.model-block__test {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding-bottom: 2px;
}

/* 结果区固定高度：按钮位置不受测试结果出现影响 */
.model-block__result {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  max-width: 240px;
  height: 16px;
  line-height: 16px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-block__result--ok {
  color: var(--accent-green);
}

.model-block__result--fail {
  color: var(--accent-red);
}

@media (max-width: 600px) {
  .model-block__body {
    flex-direction: column;
    align-items: stretch;
  }
}

/* ── 操作区 ── */
.card__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--border-subtle);
}

.card__btn-icon {
  margin-right: 6px;
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

/* 响应式：窄屏降为单列 */
@media (max-width: 720px) {
  .model-form {
    grid-template-columns: 1fr;
  }
}
</style>
