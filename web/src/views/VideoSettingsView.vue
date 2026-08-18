<!-- agent设置：视频解析 agent 的语音模型 / Chat 模型 / 解析偏好 / 运行环境 -->
<template>
  <div class="page settings-page">
    <header class="page__header">
      <h1 class="page__title">agent设置</h1>
      <span class="page__subtitle">视频解析 agent 的模型配置与环境信息</span>
    </header>

    <!-- 语音模型（ASR） -->
    <section class="card glass-surface" aria-label="语音模型">
      <div class="card__head">
        <h2 class="card__title">语音模型（ASR）</h2>
        <span class="card__badge">火山引擎语音技术</span>
      </div>
      <el-form label-position="top" class="setting-form">
        <el-form-item label="模型名">
          <el-input v-model="form.asr_model" placeholder="bigmodel（录音文件极速识别）" />
        </el-form-item>
        <el-form-item label="API Key（新版控制台）">
          <el-input
            v-model="form.asr_api_key"
            type="password"
            show-password
            :placeholder="settings.has_asr_api_key ? '已配置（留空则不变）' : '填写火山引擎语音技术 API Key'"
          />
        </el-form-item>
        <el-collapse class="legacy-collapse">
          <el-collapse-item title="旧版控制台 App ID + Access Token（可选）">
            <el-form-item label="App ID">
              <el-input
                v-model="form.asr_app_id"
                :placeholder="settings.has_asr_app_id ? '已配置（留空则不变）' : '旧版控制台 APP ID'"
              />
            </el-form-item>
            <el-form-item label="Access Token">
              <el-input
                v-model="form.asr_access_token"
                type="password"
                show-password
                :placeholder="settings.has_asr_access_token ? '已配置（留空则不变）' : '旧版控制台 Access Token'"
              />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
        <div class="form-actions">
          <el-button class="btn-press" :loading="testingAsr" @click="testAsr">测试连接</el-button>
          <el-button type="primary" class="btn-press" :loading="saving" @click="saveSettings">保存设置</el-button>
        </div>
        <p v-if="asrTestMsg" class="test-result" :class="{ ok: asrTestOk, fail: !asrTestOk }">{{ asrTestMsg }}</p>
      </el-form>
    </section>

    <!-- Chat 模型 -->
    <section class="card glass-surface" aria-label="Chat 模型">
      <div class="card__head">
        <h2 class="card__title">Chat 模型（问答/摘要）</h2>
      </div>
      <p class="card__hint">留空则复用系统模型配置；填写后视频问答与摘要使用独立模型（默认豆包方舟）。</p>
      <el-form label-position="top" class="setting-form">
        <el-form-item label="Base URL">
          <el-input v-model="form.chat_base_url" placeholder="https://ark.cn-beijing.volces.com/api/v3" />
        </el-form-item>
        <el-form-item label="模型名">
          <el-input v-model="form.chat_model" placeholder="doubao-seed-2-1-turbo-260628" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.chat_api_key"
            type="password"
            show-password
            :placeholder="settings.has_chat_api_key ? '已配置（留空则不变）' : '填写 API Key（空则复用系统）'"
          />
        </el-form-item>
        <div class="form-actions">
          <el-button class="btn-press" :loading="testingChat" @click="testChat">测试连接</el-button>
          <el-button type="primary" class="btn-press" :loading="saving" @click="saveSettings">保存设置</el-button>
        </div>
        <p v-if="chatTestMsg" class="test-result" :class="{ ok: chatTestOk, fail: !chatTestOk }">{{ chatTestMsg }}</p>
      </el-form>
    </section>

    <!-- 解析偏好 -->
    <section class="card glass-surface" aria-label="解析偏好">
      <div class="card__head">
        <h2 class="card__title">解析偏好</h2>
        <el-button text size="small" class="card__reset btn-press" @click="resetDefaults">恢复默认</el-button>
      </div>
      <el-form label-position="top" class="setting-form pref-form">
        <el-form-item label="默认关键帧数量">
          <el-radio-group v-model="form.frames">
            <el-radio-button :value="0">纯音频</el-radio-button>
            <el-radio-button :value="8">8 帧</el-radio-button>
            <el-radio-button :value="12">12 帧（推荐）</el-radio-button>
            <el-radio-button :value="24">24 帧</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <div class="form-actions">
          <el-button type="primary" class="btn-press" :loading="saving" @click="saveSettings">保存设置</el-button>
        </div>
      </el-form>
    </section>

    <!-- 运行环境 -->
    <section class="card glass-surface" aria-label="运行环境">
      <div class="card__head">
        <h2 class="card__title">运行环境</h2>
      </div>
      <dl class="info-list">
        <div class="info-row"><dt>流水线</dt><dd>内置原生流水线（不依赖外部脚本）</dd></div>
        <div class="info-row"><dt>Python 解释器</dt><dd>{{ env.python || '—' }}</dd></div>
        <div class="info-row"><dt>视频数据库目录</dt><dd>{{ env.library_root || '—' }}</dd></div>
        <div class="info-row"><dt>任务输出目录</dt><dd>{{ env.output_root || '—' }}</dd></div>
        <div class="info-row"><dt>ASR 就绪</dt><dd>{{ env.asr_configured ? '已配置' : '未配置' }}</dd></div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getVideoEnv,
  getVideoSettings,
  testVideoSettings,
  updateVideoSettings,
  type VideoEnvInfo,
  type VideoSettings,
} from '../api/video'

const env = ref<VideoEnvInfo>({})
const settings = ref<VideoSettings>({
  asr_provider: 'volcengine',
  asr_model: 'bigmodel',
  has_asr_api_key: false,
  has_asr_app_id: false,
  has_asr_access_token: false,
  chat_base_url: '',
  chat_model: '',
  has_chat_api_key: false,
  frames: 12,
})

const form = reactive({
  asr_model: 'bigmodel',
  asr_api_key: '',
  asr_app_id: '',
  asr_access_token: '',
  chat_base_url: '',
  chat_model: '',
  chat_api_key: '',
  frames: 12,
})

const saving = ref(false)
const testingAsr = ref(false)
const testingChat = ref(false)
const asrTestMsg = ref('')
const asrTestOk = ref(false)
const chatTestMsg = ref('')
const chatTestOk = ref(false)

async function load() {
  try {
    const [envData, settingsData] = await Promise.all([getVideoEnv(), getVideoSettings()])
    env.value = envData
    settings.value = settingsData
    form.asr_model = settingsData.asr_model || 'bigmodel'
    form.frames = settingsData.frames ?? 12
    form.chat_base_url = settingsData.chat_base_url || ''
    form.chat_model = settingsData.chat_model || ''
    form.asr_api_key = ''
    form.asr_app_id = ''
    form.asr_access_token = ''
    form.chat_api_key = ''
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '加载设置失败')
  }
}

async function saveSettings() {
  saving.value = true
  try {
    const updated = await updateVideoSettings({
      asr_model: form.asr_model.trim(),
      asr_api_key: form.asr_api_key,
      asr_app_id: form.asr_app_id,
      asr_access_token: form.asr_access_token,
      chat_base_url: form.chat_base_url.trim(),
      chat_model: form.chat_model.trim(),
      chat_api_key: form.chat_api_key,
      frames: form.frames,
    })
    settings.value = updated
    form.asr_api_key = ''
    form.asr_app_id = ''
    form.asr_access_token = ''
    form.chat_api_key = ''
    asrTestMsg.value = ''
    chatTestMsg.value = ''
    ElMessage.success('设置已保存')
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testAsr() {
  testingAsr.value = true
  asrTestMsg.value = ''
  try {
    const res = await testVideoSettings({
      mode: 'asr',
      asr_model: form.asr_model.trim() || undefined,
      asr_api_key: form.asr_api_key || undefined,
      asr_app_id: form.asr_app_id || undefined,
      asr_access_token: form.asr_access_token || undefined,
    })
    asrTestOk.value = res.ok
    asrTestMsg.value = res.message || (res.ok ? '连接成功' : '连接失败')
  } catch (err) {
    asrTestOk.value = false
    asrTestMsg.value = (err as { message?: string })?.message || '测试失败'
  } finally {
    testingAsr.value = false
  }
}

async function testChat() {
  testingChat.value = true
  chatTestMsg.value = ''
  try {
    const res = await testVideoSettings({
      mode: 'chat',
      chat_base_url: form.chat_base_url.trim() || undefined,
      chat_model: form.chat_model.trim() || undefined,
      chat_api_key: form.chat_api_key || undefined,
    })
    chatTestOk.value = res.ok
    chatTestMsg.value = res.message || (res.ok ? '连接成功' : '连接失败')
  } catch (err) {
    chatTestOk.value = false
    chatTestMsg.value = (err as { message?: string })?.message || '测试失败'
  } finally {
    testingChat.value = false
  }
}

function resetDefaults() {
  form.frames = 12
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

.settings-page {
  overflow-y: auto;
}

.card {
  margin-bottom: 20px;
}

.card__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.card__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.card__badge {
  padding: 2px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
}

.card__hint {
  margin: -6px 0 14px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-tertiary);
}

.setting-form {
  max-width: 640px;
}

.pref-form {
  max-width: 640px;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 4px;
}

.test-result {
  margin: 12px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
}

.test-result.ok {
  color: var(--success, #52c41a);
}

.test-result.fail {
  color: var(--danger, #f56c6c);
}

.legacy-collapse {
  margin: -6px 0 10px;
  border: none;
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
}

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
  width: 160px;
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
