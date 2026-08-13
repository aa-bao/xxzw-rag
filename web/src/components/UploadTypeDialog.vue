<template>
  <el-dialog
    :model-value="visible"
    title="选择上传类型"
    width="560px"
    append-to-body
    class="upload-type-dialog"
    @close="emit('update:visible', false)"
  >
    <p class="upload-type-dialog__intro">选择文件的组织方式，系统会进入对应的入库流程。</p>

    <div class="upload-type-dialog__grid" role="group" aria-label="上传文件类型">
      <button
        type="button"
        class="upload-type-card upload-type-card--document"
        @click="selectType('document')"
      >
        <span class="upload-type-card__icon" aria-hidden="true">
          <el-icon><Document /></el-icon>
        </span>
        <span class="upload-type-card__body">
          <span class="upload-type-card__heading">
            <span class="upload-type-card__title">普通文档</span>
            <span class="upload-type-card__badge">直接入库</span>
          </span>
          <span class="upload-type-card__description">TXT、Markdown、DOCX</span>
          <span class="upload-type-card__hint">适合正文型资料，上传后自动解析和切分。</span>
        </span>
        <el-icon class="upload-type-card__arrow" aria-hidden="true"><ArrowRight /></el-icon>
      </button>

      <button
        type="button"
        class="upload-type-card upload-type-card--structured"
        @click="selectType('structured')"
      >
        <span class="upload-type-card__icon" aria-hidden="true">
          <el-icon><DataAnalysis /></el-icon>
        </span>
        <span class="upload-type-card__body">
          <span class="upload-type-card__heading">
            <span class="upload-type-card__title">结构化数据</span>
            <span class="upload-type-card__badge">映射后入库</span>
          </span>
          <span class="upload-type-card__description">JSON、JSONL</span>
          <span class="upload-type-card__hint">适合帖子、评论等层级数据，先确认字段和关系。</span>
        </span>
        <el-icon class="upload-type-card__arrow" aria-hidden="true"><ArrowRight /></el-icon>
      </button>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ArrowRight, DataAnalysis, Document } from '@element-plus/icons-vue'

export type UploadType = 'document' | 'structured'

defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'select', value: UploadType): void
}>()

function selectType(value: UploadType) {
  emit('select', value)
  emit('update:visible', false)
}
</script>

<style scoped>
.upload-type-dialog__intro {
  margin: -4px 0 18px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.upload-type-dialog__grid {
  display: grid;
  gap: 12px;
}

.upload-type-card {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-2xl);
  background: var(--bg-subtle);
  color: var(--text-primary);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
}

.upload-type-card:hover {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 5%, var(--bg-subtle));
  transform: translateY(-1px);
}

.upload-type-card:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

.upload-type-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  border-radius: var(--radius-xl);
  background: color-mix(in srgb, var(--accent-blue) 11%, transparent);
  color: var(--accent-blue);
  font-size: 20px;
}

.upload-type-card--structured .upload-type-card__icon {
  background: color-mix(in srgb, var(--accent-indigo) 11%, transparent);
  color: var(--accent-indigo);
}

.upload-type-card__body {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.upload-type-card__heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.upload-type-card__title {
  font-size: 15px;
  font-weight: 700;
}

.upload-type-card__badge {
  padding: 2px 7px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
  color: var(--accent-blue);
  font-size: 10px;
  font-weight: 600;
}

.upload-type-card--structured .upload-type-card__badge {
  background: color-mix(in srgb, var(--accent-indigo) 10%, transparent);
  color: var(--accent-indigo);
}

.upload-type-card__description {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
}

.upload-type-card__hint {
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

.upload-type-card__arrow {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: 16px;
}

@media (prefers-reduced-motion: reduce) {
  .upload-type-card {
    transition: none;
  }
}
</style>
