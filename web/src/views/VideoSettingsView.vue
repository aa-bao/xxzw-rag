<!-- 系统设置：视频解析 agent 配置与运行信息 -->
<template>
  <div class="page settings-page">
    <header class="page__header">
      <h1 class="page__title">系统设置</h1>
      <span class="page__subtitle">视频解析 agent 的运行配置与环境信息</span>
    </header>

    <!-- 运行环境 -->
    <section class="card glass-surface" aria-label="运行环境">
      <div class="card__head">
        <h2 class="card__title">运行环境</h2>
      </div>
      <dl class="info-list">
        <div class="info-row"><dt>quick-watch 脚本</dt><dd>{{ env.script || '—' }}</dd></div>
        <div class="info-row"><dt>Python 解释器</dt><dd>{{ env.python || '—' }}</dd></div>
        <div class="info-row"><dt>视频数据库目录</dt><dd>{{ env.library_root || '—' }}</dd></div>
        <div class="info-row"><dt>任务输出目录</dt><dd>{{ env.output_root || '—' }}</dd></div>
        <div class="info-row"><dt>DashScope ASR</dt><dd>{{ env.dashscope_ready ? '已配置' : '未配置' }}</dd></div>
      </dl>
    </section>

    <!-- 解析偏好 -->
    <section class="card glass-surface" aria-label="解析偏好">
      <div class="card__head">
        <h2 class="card__title">解析偏好</h2>
        <el-button text size="small" class="card__reset btn-press" @click="resetDefaults">恢复默认</el-button>
      </div>
      <el-form label-position="top" class="pref-form">
        <el-form-item label="默认关键帧数量">
          <el-radio-group v-model="prefs.frames">
            <el-radio-button :value="0">纯音频</el-radio-button>
            <el-radio-button :value="8">8 帧</el-radio-button>
            <el-radio-button :value="12">12 帧（推荐）</el-radio-button>
            <el-radio-button :value="24">24 帧</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="转录问答模型">
          <el-input v-model="prefs.qa_model" placeholder="复用系统模型配置" disabled />
        </el-form-item>
        <div class="pref-actions">
          <el-button type="primary" class="btn-press" :loading="saving" @click="savePrefs">保存设置</el-button>
        </div>
      </el-form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getVideoEnv, getVideoPrefs, saveVideoPrefs, type VideoEnvInfo, type VideoPrefs } from '../api/video'

const env = ref<VideoEnvInfo>({})
const prefs = ref<VideoPrefs>({ frames: 12, qa_model: '' })
const saving = ref(false)

async function load() {
  try {
    env.value = await getVideoEnv()
    const loaded = await getVideoPrefs()
    prefs.value = { frames: loaded.frames ?? 12, qa_model: loaded.qa_model ?? '' }
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '加载设置失败')
  }
}

async function savePrefs() {
  saving.value = true
  try {
    await saveVideoPrefs(prefs.value)
    ElMessage.success('设置已保存')
  } catch (err) {
    ElMessage.error((err as { message?: string })?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function resetDefaults() {
  prefs.value = { frames: 12, qa_model: '' }
}

onMounted(load)
</script>

<style scoped>
.settings-page {
  overflow-y: auto;
}

.page__subtitle {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
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

.pref-form {
  max-width: 560px;
  margin-top: 8px;
}

.pref-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}
</style>
