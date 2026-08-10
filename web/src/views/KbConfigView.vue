<template>
  <div class="page">
    <header class="page__header">
      <h1 class="page__title">配置</h1>
      <span v-if="kb" class="page__subtitle">{{ kb.name }}</span>
    </header>

    <!-- 基本信息 -->
    <section v-if="kb" class="card glass-surface" v-motion="cardMotion(0)" aria-label="基本信息">
      <h2 class="card__title">基本信息</h2>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="封面">
          <div class="cover-row">
            <div class="cover-preview">
              <img
                v-if="kb.cover_url"
                :src="kb.cover_url"
                class="cover-preview__img"
                alt="知识库封面"
              />
              <el-icon v-else class="cover-preview__placeholder" aria-hidden="true"><Picture /></el-icon>
            </div>
            <el-upload
              :show-file-list="false"
              :auto-upload="false"
              accept=".jpg,.jpeg,.png,.webp,.gif"
              :on-change="handleCoverChange"
            >
              <el-button class="btn-press" :loading="uploadingCover">
                <el-icon class="btn-icon" aria-hidden="true"><Upload /></el-icon>
                <span>{{ kb.cover_url ? '更换封面' : '上传封面' }}</span>
              </el-button>
            </el-upload>
          </div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="知识库名称" aria-label="知识库名称" maxlength="60" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            placeholder="用一句话描述这个知识库的内容"
            aria-label="知识库描述"
          />
        </el-form-item>
        <div class="form-actions">
          <el-button type="primary" class="btn-press" :loading="saving" @click="saveInfo">
            <span>保存</span>
          </el-button>
        </div>
      </el-form>
    </section>

    <!-- 嵌入配置（只读） -->
    <section v-if="kb" class="card glass-surface" v-motion="cardMotion(1)" aria-label="嵌入配置">
      <div class="card__head">
        <h2 class="card__title">嵌入配置</h2>
        <span class="card__tag">只读</span>
      </div>
      <div class="kv-grid">
        <div class="kv-item">
          <span class="kv-label">嵌入模型</span>
          <span class="kv-value">{{ displayText(kb.embedding_model) }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-label">向量维度</span>
          <span class="kv-value kv-value--num">{{ displayNum(kb.embedding_dimension) }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-label">Chunk 大小</span>
          <span class="kv-value kv-value--num">{{ displayNum(kb.chunk_size) }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-label">Overlap</span>
          <span class="kv-value kv-value--num">{{ displayNum(kb.overlap) }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-label">Top K</span>
          <span class="kv-value kv-value--num">{{ displayNum(kb.top_k) }}</span>
        </div>
        <div class="kv-item">
          <span class="kv-label">相似度阈值</span>
          <span class="kv-value kv-value--num">{{ displayNum(kb.similarity_threshold) }}</span>
        </div>
      </div>
    </section>

    <!-- 危险操作 -->
    <section v-if="kb" class="card card--danger glass-surface" v-motion="cardMotion(2)" aria-label="危险操作">
      <h2 class="card__title card__title--danger">
        <el-icon class="card__title-icon" aria-hidden="true"><WarningFilled /></el-icon>
        <span>危险操作</span>
      </h2>
      <p class="danger__desc">
        删除知识库将永久移除该知识库下的全部文档、向量索引与会话记录，此操作不可恢复。
      </p>
      <button type="button" class="danger__btn btn-press" :disabled="deleting" @click="confirmDelete">
        <el-icon class="danger__btn-icon" aria-hidden="true"><Delete /></el-icon>
        <span>{{ deleting ? '删除中' : '删除知识库' }}</span>
      </button>
    </section>

    <!-- 加载中 -->
    <div v-if="loading" class="page__loading" v-loading="true" aria-label="加载中"></div>

    <!-- 加载失败 -->
    <section v-else-if="loadError" class="card glass-surface page__error" v-motion="cardMotion(0)">
      <el-icon class="page__error-icon" aria-hidden="true"><Warning /></el-icon>
      <p class="page__error-text">知识库加载失败，请稍后重试</p>
      <el-button type="primary" class="btn-press" @click="load">重新加载</el-button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Delete, Picture, Upload, Warning, WarningFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteKb, getKb, updateKb, uploadKbCover } from '../api/kb'
import type { KbInfo } from '../api/kb'

const route = useRoute()
const router = useRouter()

const kbId = Number(route.params.id)

const kb = ref<KbInfo | null>(null)
const loading = ref(true)
const loadError = ref(false)
const saving = ref(false)
const deleting = ref(false)

const form = reactive({ name: '', description: '' })
async function load() {
  loading.value = true
  loadError.value = false
  try {
    kb.value = await getKb(kbId)
    form.name = kb.value.name
    form.description = kb.value.description ?? ''
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

/* ── 封面上传 ── */
const uploadingCover = ref(false)

async function handleCoverChange(file: { raw?: File }) {
  const raw = file.raw
  if (!raw || uploadingCover.value || !kb.value) return
  uploadingCover.value = true
  try {
    kb.value = await uploadKbCover(kbId, raw)
    ElMessage.success('封面已更新')
  } catch (err) {
    const message = (err as { error?: { message?: string } })?.error?.message
    ElMessage.error(message || '封面上传失败')
  } finally {
    uploadingCover.value = false
  }
}

async function saveInfo() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  saving.value = true
  try {
    kb.value = await updateKb(kbId, { name, description: form.description.trim() })
    ElMessage.success('已保存')
  } catch {
    ElMessage.error('保存失败，请重试')
  } finally {
    saving.value = false
  }
}

async function confirmDelete() {
  if (!kb.value) return
  try {
    await ElMessageBox.confirm(
      `确定要删除知识库「${kb.value.name}」吗？删除后将永久移除该知识库下的全部文档、向量索引与会话记录，此操作不可恢复。`,
      '删除知识库',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'confirm-danger',
      },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteKb(kbId)
    ElMessage.success('知识库已删除')
    router.push('/kb')
  } catch {
    ElMessage.error('删除失败，请重试')
  } finally {
    deleting.value = false
  }
}

/* ── 展示格式化 ── */
function displayText(value: string | null | undefined): string {
  return value && value.trim() ? value : '—'
}

function displayNum(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return String(value)
}

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
  font-size: 14px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 玻璃卡片 ── */
.card {
  max-width: 680px;
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
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.card__head .card__title {
  margin-bottom: 0;
}

.card__title-icon {
  font-size: 16px;
}

.card__tag {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

/* ── 危险操作：红色警示 ── */
.card--danger {
  border-color: color-mix(in srgb, var(--accent-red) 22%, transparent);
}

.card__title--danger {
  color: var(--accent-red);
}

.danger__desc {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.danger__btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border: 1px solid var(--accent-red);
  border-radius: var(--radius-lg);
  background: transparent;
  color: var(--accent-red);
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.danger__btn:hover {
  background: color-mix(in srgb, var(--accent-red) 8%, transparent);
}

.danger__btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.danger__btn-icon {
  font-size: 16px;
}

/* ── 表单 ── */
.form-actions {
  display: flex;
  justify-content: flex-end;
}

/* ── 封面上传 ── */
.cover-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.cover-preview {
  width: 96px;
  height: 96px;
  border-radius: var(--radius-2xl);
  border: 1px solid var(--border-subtle);
  background: var(--bg-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: var(--shadow-card);
  overflow: hidden;
}

/* object-fit 方案：无背景定位亚像素误差，1px 边框内完美铺满 */
.cover-preview__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  display: block;
}

.cover-preview__placeholder {
  font-size: 32px;
  color: var(--text-tertiary);
}

.btn-icon {
  margin-right: 6px;
}

/* ── 只读 label-value 网格（参考旧 KbDetailView 设置区） ── */
.kv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px 24px;
}

.kv-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.kv-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.kv-value {
  font-size: 14px;
  color: var(--text-primary);
  overflow-wrap: break-word;
}

.kv-value--num {
  font-variant-numeric: tabular-nums;
}

/* ── 加载 / 错误 ── */
.page__loading {
  min-height: 200px;
}

.page__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 20px;
}

.page__error-icon {
  font-size: 40px;
  color: var(--text-tertiary);
}

.page__error-text {
  font-size: 13px;
  color: var(--text-secondary);
}

/* MessageBox 危险确认按钮（teleport 到 body，需 global） */
:global(.confirm-danger) {
  --el-button-bg-color: var(--accent-red);
  --el-button-border-color: var(--accent-red);
  --el-button-hover-bg-color: color-mix(in srgb, var(--accent-red) 85%, var(--bg-elevated));
  --el-button-hover-border-color: color-mix(in srgb, var(--accent-red) 85%, var(--bg-elevated));
  --el-button-active-bg-color: var(--accent-red);
}
</style>
