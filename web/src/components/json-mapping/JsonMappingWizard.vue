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
      <div v-else-if="state.step === 'fields'" class="wizard-coming">
        字段映射步骤（下一步实现）
      </div>
      <div v-else-if="state.step === 'relations'" class="wizard-coming">
        关系与层级步骤（下一步实现）
      </div>
      <div v-else-if="state.step === 'preview'" class="wizard-coming">
        预览步骤（下一步实现）
      </div>
      <div v-else class="wizard-coming">确认与入库步骤（下一步实现）</div>
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
import { ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import JsonUploadStep from './JsonUploadStep.vue'
import JsonStructureStep from './JsonStructureStep.vue'
import { useJsonMappingWizard } from './useJsonMappingWizard'
import { uploadDoc } from '../../api/docs'
import type { SourceProfile } from '../../types/structured'

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

const stepNumber = computed(() => hook.stepNumber.value)
const hasFile = computed(() => state.value.step === 'upload' && state.value.file !== null)

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
    default:
      return ''
  }
})

const primaryDisabled = computed(() => {
  if (busy.value !== null) return true
  const s = state.value
  if (s.step === 'upload') return !hasFile.value
  if (s.step === 'structure') return structureStepRef.value?.selectedPath === null
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
    } catch (err) {
      errorText.value = getErrorMessage(err)
    }
    return
  }
  if (s.step === 'preview') {
    try {
      hook.next()
    } catch (err) {
      errorText.value = getErrorMessage(err)
    }
  }
}

/** 结构步骤候选选择（卡片点击直接选中，不直接进入下一步） */
function onCandidateSelect() {
  errorText.value = ''
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
  hook.back()
}

/** 上传后（已进入向导）关闭需确认；HTTP 请求期间禁止关闭 */
async function handleCloseRequest() {
  if (busy.value !== null) return
  if (state.value.step === 'upload' && !hasFile.value) {
    emit('close')
    return
  }
  try {
    await ElMessageBox.confirm(
      '确定关闭向导吗？已上传的 JSON 文件会保留在文档列表中，但尚未配置映射。',
      '关闭向导',
      { confirmButtonText: '关闭', cancelButtonText: '继续配置', type: 'warning' },
    )
    emit('close')
  } catch {
    /* 用户取消，继续向导 */
  }
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
