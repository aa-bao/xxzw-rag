<template>
  <el-dialog
    :model-value="visible"
    title="JSON 映射向导"
    width="720px"
    class="json-wizard"
    :close-on-click-modal="!busy"
    :close-on-press-escape="!busy"
    :show-close="false"
    append-to-body
    @update:model-value="handleCloseRequest"
  >
    <!-- 六步头部 -->
    <div class="wizard-steps" role="tablist" aria-label="向导步骤">
      <div
        v-for="(label, idx) in STEP_LABELS"
        :key="label"
        class="wizard-step"
        :class="{
          'wizard-step--active': idx + 1 === stepNumber,
          'wizard-step--done': idx + 1 < stepNumber,
        }"
        role="tab"
        :aria-selected="idx + 1 === stepNumber"
        aria-disabled="true"
      >
        <span class="wizard-step__num kpi-num">{{ idx + 1 }}</span>
        <span class="wizard-step__label">{{ label }}</span>
      </div>
    </div>

    <!-- 当前步骤内容 -->
    <div class="wizard-body">
      <JsonUploadStep
        v-if="state.step === 'upload'"
        :files="uploadFiles"
        :busy="busy"
        :error="errorText"
        @pick="handlePickFiles"
        @clear="handleRemoveFile"
      />
      <!-- 批量上传进度与状态（由 uploadEntries 驱动） -->
      <div v-if="state.step === 'upload'" class="json-wizard-upload">
        <div v-if="busy === 'upload'" class="json-wizard-upload__progress" role="status">
          上传中 {{ uploadingIndex }}/{{ uploadTotal }}（{{ uploadingName }}）
        </div>
        <div v-if="settledEntries.length > 0" class="json-wizard-upload__entries" aria-label="上传状态">
          <div
            v-for="e in settledEntries"
            :key="e.file.name"
            class="json-wizard-upload__entry"
          >
            <span class="json-wizard-upload__name">{{ e.file.name }}</span>
            <span
              v-if="e.status === 'uploaded'"
              class="json-wizard-upload__pill json-wizard-upload__pill--ok"
            >已上传</span>
            <span
              v-else-if="e.status === 'failed'"
              class="json-wizard-upload__pill json-wizard-upload__pill--err"
              :title="e.error ?? undefined"
            >{{ e.error ?? '上传失败' }}</span>
          </div>
        </div>
      </div>
      <JsonStructureStep
        v-else-if="state.step === 'structure'"
        :profile="state.profile"
        :busy="busy"
        :error="errorText"
        ref="structureStepRef"
        @select="onCandidateSelect"
      />
      <JsonFieldMappingStep
        v-else-if="state.step === 'fields'"
        :profile="state.profile"
        :mapping="state.mapping"
        ref="fieldsStepRef"
        @update:mapping="onMappingUpdated"
      />
      <JsonRelationStep
        v-else-if="state.step === 'relations'"
        :mapping="state.mapping"
        @update:mapping="onMappingUpdated"
      />
      <JsonPreviewStep
        v-else-if="state.step === 'preview'"
        :rows="state.rows"
        :total-rows="previewTotal"
        :global-warnings="previewWarnings"
        :error="previewError"
      />
      <JsonConfirmStep
        v-else-if="state.step === 'confirm'"
        :mapping="state.mapping"
        :templates="templates"
        :total-rows="previewTotal"
        :warning-count="confirmWarningCount"
        :compatibility="compatibility"
        :files="batchDocs ?? undefined"
        ref="confirmStepRef"
      />
    </div>

    <!-- 底部：返回 + 主操作 -->
    <template #footer>
      <div class="wizard-footer">
        <el-button
          v-if="stepNumber > 1"
          class="btn-press"
          :disabled="busy !== null"
          @click="handleBack"
        >
          返回
        </el-button>
        <span class="wizard-footer__spacer" aria-hidden="true"></span>
        <el-button class="btn-press" :disabled="busy !== null" @click="handleCloseRequest">
          {{ hasFile ? '关闭向导' : '取消' }}
        </el-button>
        <el-button
          type="primary"
          class="btn-press"
          :disabled="primaryDisabled"
          @click="handlePrimary"
        >
          <el-icon v-if="busy !== null" class="is-loading" aria-hidden="true"><Loading /></el-icon>
          {{ primaryLabel }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import JsonUploadStep from './JsonUploadStep.vue'
import JsonStructureStep from './JsonStructureStep.vue'
import JsonFieldMappingStep from './JsonFieldMappingStep.vue'
import JsonRelationStep from './JsonRelationStep.vue'
import JsonPreviewStep from './JsonPreviewStep.vue'
import JsonConfirmStep from './JsonConfirmStep.vue'
import { useJsonMappingWizard } from './useJsonMappingWizard'
import { checkMappingCompatibility, listMappingTemplates, createMappingTemplateVersion } from '../../api/structured'
import { uploadDoc } from '../../api/docs'
import type { BatchDocEntry } from './useJsonMappingWizard'
import type { Compatibility, MappingTemplateSummary } from '../../types/structured'

const props = defineProps<{
  visible: boolean
  kbId: number
  /** 继续配置模式：预置的已上传文档 ID（awaiting_mapping/failed 文档重新进入向导） */
  docId?: number | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'ingested', value: { docId: number; jobId: number; name?: string; fileSizeBytes?: number | null }): void
}>()

const STEP_LABELS = ['上传文件', '结构检测', '字段映射', '关系与层级', '真实预览', '确认入库']

const hook = useJsonMappingWizard(props.kbId)
const state = hook.state
const busy = hook.busy
const batchDocs = hook.batchDocs
const errorText = ref('')

/** 上传步骤条目：批量上传流的文件级状态（单文件模式同样经此流转） */
interface UploadEntry {
  file: File
  docId: number | null
  status: 'pending' | 'uploading' | 'uploaded' | 'failed'
  error: string | null
}
const uploadEntries = ref<UploadEntry[]>([])
/** 本批待上传总数（进度展示分母） */
const uploadTotal = ref(0)

const structureStepRef = ref<InstanceType<typeof JsonStructureStep> | null>(null)
const fieldsStepRef = ref<InstanceType<typeof JsonFieldMappingStep> | null>(null)
const confirmStepRef = ref<InstanceType<typeof JsonConfirmStep> | null>(null)

/** 预览相关 transient 数据（映射变更后清空） */
const previewTotal = ref(0)
const previewWarnings = ref<string[]>([])
const previewError = ref<{ message: string; source_pointer: string | null } | null>(null)
const templates = ref<MappingTemplateSummary[]>([])
const compatibility = ref<Compatibility | null>(null)

const stepNumber = computed(() => hook.stepNumber.value)
/** 有待上传/重试的文件（含失败项）；state.files 由 hook 在上传后同步 */
const hasFile = computed(
  () =>
    state.value.step === 'upload' &&
    (state.value.files.length > 0 || uploadEntries.value.some((e) => e.status !== 'uploaded')),
)
/** 继续配置模式：外部传入的已上传文档 ID */
const resumeDocId = computed(() => props.docId ?? null)
/** 继续配置模式：未选择文件但有预置文档，可直接开始探查 */
const canResumeProfile = computed(
  () => state.value.step === 'upload' && state.value.files.length === 0 && resumeDocId.value !== null,
)
/** 批量已上传完毕（back 回 upload 且列表已清空）：可直接重新探查批次首个文档 */
const canResumeBatch = computed(
  () =>
    state.value.step === 'upload' &&
    state.value.files.length === 0 &&
    uploadEntries.value.length === 0 &&
    batchDocs.value !== null,
)
/** 批量批次中的失败项数量（confirm 步骤重试提示） */
const batchFailedCount = computed(
  () => batchDocs.value?.filter((e) => e.status === 'failed').length ?? 0,
)
/** 上传列表文件（与 uploadEntries 同序，clear 下标对齐） */
const uploadFiles = computed(() => uploadEntries.value.map((e) => e.file))
/** 已完成（成功/失败）的上传条目：仅渲染有状态的项 */
const settledEntries = computed(() =>
  uploadEntries.value.filter((e) => e.status === 'uploaded' || e.status === 'failed'),
)
/** 当前上传进度：uploading 条目在列表中的序号（1 起），n 为本批待上传总数 */
const uploadingIndex = computed(() => {
  const idx = uploadEntries.value.findIndex((e) => e.status === 'uploading')
  return idx >= 0 ? idx + 1 : uploadTotal.value
})
const uploadingName = computed(
  () => uploadEntries.value.find((e) => e.status === 'uploading')?.file.name ?? '',
)
const confirmWarningCount = computed(() =>
  state.value.step === 'preview' || state.value.step === 'confirm'
    ? state.value.rows.reduce((n, r) => n + r.warnings.length, 0)
    : 0,
)

const primaryLabel = computed(() => {
  switch (state.value.step) {
    case 'upload':
      return hasFile.value || canResumeProfile.value || canResumeBatch.value ? '开始探查' : '下一步'
    case 'structure':
      return '使用所选路径'
    case 'fields':
      return '下一步'
    case 'relations':
      return '预览'
    case 'preview':
      return '下一步'
    case 'confirm':
      return batchFailedCount.value > 0 ? `重试失败项(${batchFailedCount.value})` : '确认入库'
    default:
      return ''
  }
})

const primaryDisabled = computed(() => {
  if (busy.value !== null) return true
  const s = state.value
  if (s.step === 'upload') return !hasFile.value && !canResumeProfile.value && !canResumeBatch.value
  if (s.step === 'structure') return structureStepRef.value?.selectedPath === null
  if (s.step === 'fields') return fieldsStepRef.value?.canContinue !== true
  if (s.step === 'confirm') return !confirmStepRef.value?.checked
  return false
})

/* ── 上传步骤 ── */
function handlePickFiles(files: File[]) {
  errorText.value = ''
  // 不可变追加：新文件一律待处理
  uploadEntries.value = [
    ...uploadEntries.value,
    ...files.map((f) => ({ file: f, docId: null, status: 'pending' as const, error: null })),
  ]
}

function handleRemoveFile(i: number) {
  const entry = uploadEntries.value[i]
  if (!entry) return
  uploadEntries.value = uploadEntries.value.filter((_, idx) => idx !== i)
  if (entry.docId !== null) {
    // 已上传：同步移除批次项（删光则置 null）
    const docs = batchDocs.value
    if (docs !== null) {
      const next = docs.filter((d) => d.docId !== entry.docId)
      if (next.length > 0) {
        hook.setBatchDocs(next)
      } else {
        batchDocs.value = null
        hook.setFiles([])
      }
    }
  } else if (batchDocs.value === null) {
    // 未上传且非批量模式：与 hook 单文件状态保持同步
    hook.setFiles([])
  }
}

/** 批量上传流：逐个上传待处理文件，单文件失败标记但不中断；全部成功后探查首个文档 */
async function startBatchUpload() {
  const failed = uploadEntries.value.filter((e) => e.status === 'failed')
  if (failed.length > 0) {
    try {
      await ElMessageBox.confirm(
        `${failed.length} 个文件上传失败：${failed.map((e) => e.file.name).join('、')}。跳过失败文件继续？`,
        '上传失败',
        { confirmButtonText: '跳过并继续', cancelButtonText: '返回处理', type: 'warning' },
      )
    } catch {
      return
    }
  }
  const pending = uploadEntries.value.filter((e) => e.status !== 'uploaded')
  if (pending.length === 0) {
    // 全部已上传（如探查失败后返回）：直接重新探查批次首个文档
    const docs = batchDocs.value
    if (docs !== null && docs.length > 0 && busy.value === null) {
      try {
        await hook.profile(docs[0].docId)
      } catch (err) {
        errorText.value = getErrorMessage(err)
      }
    }
    return
  }
  if (busy.value !== null) return
  const okEntries: BatchDocEntry[] = []
  errorText.value = ''
  uploadTotal.value = pending.length
  busy.value = 'upload'
  try {
    for (let i = 0; i < pending.length; i++) {
      const entry = pending[i]
      entry.status = 'uploading'
      try {
        const result = await uploadDoc(props.kbId, entry.file)
        entry.docId = result.doc_id
        entry.status = 'uploaded'
        okEntries.push({
          docId: result.doc_id,
          name: entry.file.name,
          fileSizeBytes: entry.file.size,
          status: 'pending',
          jobId: null,
          error: null,
        })
      } catch (err) {
        entry.status = 'failed'
        entry.error = getErrorMessage(err)
      }
    }
    if (okEntries.length === 0) {
      errorText.value = '所有文件上传失败，请重试或移除后重新选择'
      return
    }
  } finally {
    busy.value = null
  }
  // 让 hook 感知批次文件（profile 校验 files.length > 0），再入库批次
  hook.setFiles(uploadEntries.value.filter((e) => e.status === 'uploaded').map((e) => e.file))
  hook.setBatchDocs(okEntries)
  try {
    await hook.profile(okEntries[0].docId)
  } catch (err) {
    errorText.value = getErrorMessage(err)
  }
}

/* ── 主操作 ── */
async function handlePrimary() {
  const s = state.value
  if (busy.value !== null) return
  if (s.step === 'upload') {
    // 继续配置模式（单文档 resume）：跳过上传直接探查已上传文档
    if (s.files.length === 0 && resumeDocId.value !== null) {
      try {
        await hook.profile(resumeDocId.value)
      } catch (err) {
        errorText.value = getErrorMessage(err)
      }
      return
    }
    // 批量已上传完毕且列表已清空：直接探查批次首个文档
    if (s.files.length === 0 && canResumeBatch.value) {
      try {
        await hook.profile(batchDocs.value![0].docId)
      } catch (err) {
        errorText.value = getErrorMessage(err)
      }
      return
    }
    await startBatchUpload()
    return
  }
  if (s.step === 'structure') {
    const path = structureStepRef.value?.selectedPath
    if (path === null) return
    const candidate = s.profile.candidates.find((c) => c.record_path === path)
    if (!candidate) return
    // 已有字段编辑时换候选需确认（当前本批次从无编辑进入，保持钩子供后续任务使用）
    const confirmed = await confirmCandidateSwitch(candidate.record_path)
    if (!confirmed) return
    hook.selectCandidate(s.profile, candidate.suggested_mapping)
    return
  }
  if (s.step === 'fields') {
    try {
      hook.next()
    } catch (err) {
      errorText.value = getErrorMessage(err)
    }
    return
  }
  if (s.step === 'relations') {
    try {
      const response = await hook.preview(s.mapping)
      const preview = state.value
      if (preview.step === 'preview') {
        previewTotal.value = response.total_rows
        previewWarnings.value = response.warnings
        previewError.value = null
      }
    } catch (err) {
      // 后端预览错误：留在本步骤并指向出错记录
      errorText.value = getErrorMessage(err)
      previewError.value = {
        message: errorText.value,
        source_pointer: previewErrorSource(err),
      }
    }
    return
  }
  if (s.step === 'preview') {
    try {
      hook.next()
      await loadConfirmState()
    } catch (err) {
      errorText.value = getErrorMessage(err)
    }
    return
  }
  if (s.step === 'confirm') {
    await handleConfirmIngest()
  }
}

/** 从后端错误中提取 source_pointer（预览错误定位） */
function previewErrorSource(err: unknown): string | null {
  if (err && typeof err === 'object') {
    const e = err as { error?: { source_pointer?: string | null } }
    return e.error?.source_pointer ?? null
  }
  return null
}

/** 进入确认步骤：加载模板列表 + 兼容性检查 */
async function loadConfirmState() {
  try {
    const [templateList] = await Promise.all([listMappingTemplates()])
    templates.value = templateList
  } catch (err) {
    templates.value = []
    // 模板加载失败不阻塞入库（可新建模板）
    console.warn('映射模板加载失败', err)
  }
  compatibility.value = null
  const s = state.value
  if (s.step !== 'confirm') return
  // 以当前映射检查与所选模板的兼容性：默认选第一个模板（如有）
  if (templates.value.length > 0) {
    try {
      compatibility.value = await checkMappingCompatibility(
        props.kbId,
        s.docId,
        s.mapping,
      )
    } catch {
      compatibility.value = null
    }
  }
}

/** 确认入库：创建/复用模板版本 → ingest（防双提交由 busy 保证） */
async function handleConfirmIngest() {
  if (busy.value !== null) return
  const step = confirmStepRef.value
  const s = state.value
  if (s.step !== 'confirm' || !step) return
  if (!step.checked) return
  if (step.needsReconfirm && !step.breakingConfirmed) {
    errorText.value = '请先确认破坏性变更后再入库'
    return
  }
  const { template_id, name } = step.submitPayload()
  try {
    busy.value = 'create_version'
    const created = await createMappingTemplateVersion({ template_id, name, mapping: s.mapping })
    busy.value = null
    const results = await hook.confirmIngest(created.mapping_version_id)
    // 逐条通知文档列表（追加文档行并轮询状态）；带真实文件名与大小供列表展示
    for (const r of results) {
      const entry = batchDocs.value?.find((e) => e.docId === r.doc_id)
      emit('ingested', {
        docId: r.doc_id,
        jobId: r.job_id,
        name: entry?.name,
        fileSizeBytes: entry?.fileSizeBytes ?? null,
      })
    }
    const failedCount = batchFailedCount.value
    if (failedCount > 0) {
      ElMessage.warning(`已提交 ${results.length} 个文档，${failedCount} 个失败，可重试失败项`)
    } else {
      ElMessage.success(`已提交 ${results.length} 个文档的入库任务（模板 v${created.version}）`)
      // 全部成功：关闭向导（KbDocsView 已不再负责关闭）
      emit('close')
    }
  } catch (err) {
    errorText.value = getErrorMessage(err)
  } finally {
    busy.value = null
  }
}

/** 结构步骤候选选择（卡片点击直接选中，不直接进入下一步） */
function onCandidateSelect() {
  errorText.value = ''
}

/**
 * 字段/关系步骤的受控映射更新：
 * fields/relations 整体替换 state.mapping；若在 preview/confirm（映射被再次编辑），
 * 则由状态机的 previewHash 失效机制要求重新预览。
 */
function onMappingUpdated(mapping: import('../../types/structured').MappingDefinition) {
  const s = state.value
  if (s.step === 'fields' || s.step === 'relations') {
    hook.setStateMapping(mapping)
    return
  }
  hook.applyMappingUpdate(mapping)
}

async function confirmCandidateSwitch(path: string): Promise<boolean> {
  if (!fieldEditsExist.value) return true
  try {
    await ElMessageBox.confirm(
      `切换候选记录路径将重新生成字段映射，当前对字段的编辑会被覆盖。确定切换到「${path}」吗？`,
      '切换候选',
      { confirmButtonText: '切换', cancelButtonText: '取消', type: 'warning' },
    )
    return true
  } catch {
    return false
  }
}

/** 是否已存在字段编辑（Task 3 接通 fields 步骤后由子组件上报；当前恒为 false） */
const fieldEditsExist = ref(false)

function handleBack() {
  errorText.value = ''
  previewError.value = null
  hook.back()
}

/** 上传后（已进入向导）关闭需确认；HTTP 请求期间禁止关闭 */
async function handleCloseRequest() {
  if (busy.value !== null) return
  if (state.value.step === 'upload' && !hasFile.value) {
    resetTransient()
    emit('close')
    return
  }
  try {
    await ElMessageBox.confirm(
      '确定关闭向导吗？已上传的 JSON 文件会保留在文档列表中，但尚未配置映射。',
      '关闭向导',
      { confirmButtonText: '关闭', cancelButtonText: '继续配置', type: 'warning' },
    )
    resetTransient()
    emit('close')
  } catch {
    /* 用户取消，继续向导 */
  }
}

/** 清空预览/模板等 transient 数据（关闭向导或重置时） */
function resetTransient() {
  previewTotal.value = 0
  previewWarnings.value = []
  previewError.value = null
  templates.value = []
  compatibility.value = null
}

function getErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'error' in err) {
    const apiError = (err as { error?: { message?: unknown } }).error
    if (typeof apiError?.message === 'string' && apiError.message) return apiError.message
  }
  if (err && typeof err === 'object' && 'message' in err) {
    const message = (err as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请重试'
}

/** 向导打开时重置状态机（全新会话，支持多文件批量） */
watch(
  () => props.visible,
  (v) => {
    if (v) {
      hook.setFiles([])
      uploadEntries.value = []
      uploadTotal.value = 0
      if (resumeDocId.value !== null) {
        hook.setDocForResume(resumeDocId.value)
      }
      errorText.value = ''
      fieldEditsExist.value = false
      resetTransient()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.wizard-steps {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 0 16px;
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: 16px;
}

.wizard-step {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--radius-full);
  color: var(--text-tertiary);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.wizard-step__num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-tertiary) 14%, transparent);
  font-size: 11px;
  font-weight: 700;
}

.wizard-step--done {
  color: var(--accent-green);
}

.wizard-step--done .wizard-step__num {
  background: color-mix(in srgb, var(--accent-green) 14%, transparent);
}

.wizard-step--active {
  color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 10%, transparent);
}

.wizard-step--active .wizard-step__num {
  background: var(--accent-blue);
  color: #fff;
}

.wizard-body {
  min-height: 300px;
  max-height: 56vh;
  overflow-y: auto;
}

.wizard-coming {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: var(--text-tertiary);
  font-size: 13px;
}

.wizard-footer {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wizard-footer__spacer {
  flex: 1;
}

.json-wizard-upload {
  margin-top: 12px;
}

.json-wizard-upload__progress {
  font-size: 12px;
  color: var(--accent-blue);
  margin-bottom: 8px;
}

.json-wizard-upload__entries {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.json-wizard-upload__entry {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.json-wizard-upload__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.json-wizard-upload__pill {
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--text-secondary) 10%, transparent);
  color: var(--text-secondary);
  white-space: nowrap;
}

.json-wizard-upload__pill--ok {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.json-wizard-upload__pill--err {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}
</style>
