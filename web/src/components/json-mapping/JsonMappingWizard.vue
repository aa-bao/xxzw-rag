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
        :file="state.file"
        :busy="busy"
        :error="errorText"
        @pick="handlePick"
        @clear="handleClear"
      />
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
        :total-rows="state.rows.length"
        :warning-count="confirmWarningCount"
        :compatibility="compatibility"
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
          v-if="stepNumber < 6"
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
import type { Compatibility, MappingTemplateSummary } from '../../types/structured'

const props = defineProps<{
  visible: boolean
  kbId: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'ingested', value: { docId: number; jobId: number }): void
}>()

const STEP_LABELS = ['上传文件', '结构检测', '字段映射', '关系与层级', '真实预览', '确认入库']

const hook = useJsonMappingWizard(props.kbId)
const state = hook.state
const busy = hook.busy
const errorText = ref('')

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
const hasFile = computed(() => state.value.step === 'upload' && state.value.file !== null)
const confirmWarningCount = computed(() =>
  state.value.step === 'preview' || state.value.step === 'confirm'
    ? state.value.rows.reduce((n, r) => n + r.warnings.length, 0)
    : 0,
)

const primaryLabel = computed(() => {
  switch (state.value.step) {
    case 'upload':
      return hasFile.value ? '开始探查' : '下一步'
    case 'structure':
      return '使用所选路径'
    case 'fields':
      return '下一步'
    case 'relations':
      return '预览'
    case 'preview':
      return '下一步'
    case 'confirm':
      return '确认入库'
    default:
      return ''
  }
})

const primaryDisabled = computed(() => {
  if (busy.value !== null) return true
  const s = state.value
  if (s.step === 'upload') return !hasFile.value
  if (s.step === 'structure') return structureStepRef.value?.selectedPath === null
  if (s.step === 'fields') return fieldsStepRef.value?.canContinue !== true
  if (s.step === 'confirm') return !confirmStepRef.value?.checked
  return false
})

/* ── 上传步骤 ── */
function handlePick(file: File) {
  errorText.value = ''
  hook.setFile(file)
}

function handleClear() {
  hook.setFile(null)
}

/** 走现有文档上传端点（/kb/{kb_id}/docs/upload），返回 doc_id */
async function uploadFile(file: File): Promise<number | null> {
  try {
    const result = await uploadDoc(props.kbId, file)
    return result.doc_id
  } catch (err) {
    errorText.value = getErrorMessage(err)
    return null
  }
}

/* ── 主操作 ── */
async function handlePrimary() {
  const s = state.value
  if (busy.value !== null) return
  if (s.step === 'upload') {
    if (s.file === null) return
    const docId = await uploadFile(s.file)
    if (docId === null) return
    try {
      await hook.profile(docId)
    } catch (err) {
      errorText.value = getErrorMessage(err)
    }
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
      await hook.preview(s.mapping)
      const preview = state.value
      if (preview.step === 'preview') {
        previewTotal.value = preview.rows.length
        previewWarnings.value = []
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
    const ingested = await hook.confirmIngest(created.mapping_version_id)
    void ingested
    ElMessage.success(`入库任务已提交（模板 v${created.version}）`)
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
  if (err && typeof err === 'object' && 'message' in err) {
    const message = (err as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (err instanceof Error) return err.message
  return '操作失败，请重试'
}

/** 向导打开时重置状态机（全新会话，一次一个文件） */
watch(
  () => props.visible,
  (v) => {
    if (v) {
      hook.setFile(null)
      errorText.value = ''
      fieldEditsExist.value = false
      resetTransient()
    }
  },
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
</style>
