import { computed, ref } from 'vue'
import type {
  IngestResult,
  MappingDefinition,
  PreviewResponse,
  PreviewRow,
  SourceProfile,
} from '../../types/structured'
import { profileJson, previewJson, startJsonIngest } from '../../api/structured'

/**
 * JSON 映射向导状态机：六个编号步骤，判别式联合状态。
 * 显式转换方法 + 单一 busy 操作名（防双提交）。
 *
 * 步骤编号：
 *   1 upload → 2 structure → 3 fields → 4 relations → 5 preview → 6 confirm
 */

export type WizardStep = 'upload' | 'structure' | 'fields' | 'relations' | 'preview' | 'confirm'

export type BatchIngestStatus = 'pending' | 'ingested' | 'failed'

export interface BatchDocEntry {
  docId: number
  name: string
  status: BatchIngestStatus
  jobId: number | null
  error: string | null
}

export type WizardState =
  | { step: 'upload'; files: File[] }
  | { step: 'structure'; docId: number; profile: SourceProfile }
  | { step: 'fields'; docId: number; profile: SourceProfile; mapping: MappingDefinition }
  | { step: 'relations'; docId: number; mapping: MappingDefinition }
  | { step: 'preview'; docId: number; mapping: MappingDefinition; rows: PreviewRow[] }
  | { step: 'confirm'; docId: number; mapping: MappingDefinition; rows: PreviewRow[] }

export type BusyOperation =
  | 'upload'
  | 'profile'
  | 'preview'
  | 'create_version'
  | 'ingest'

const STEP_ORDER: WizardStep[] = ['upload', 'structure', 'fields', 'relations', 'preview', 'confirm']

function stepNumber(step: WizardStep): number {
  return STEP_ORDER.indexOf(step) + 1
}

/** 每个被索引（非 ignore）的 record type 必须至少有一个 title 或 content 字段 */
export function hasRequiredIndexedFields(mapping: MappingDefinition): boolean {
  return mapping.record_types
    .filter((rt) => rt.chunk_policy !== 'ignore')
    .every(
      (rt) =>
        rt.fields.some((f) => f.role === 'title' || f.role === 'content') ||
        rt.children.some((c) => c.fields.some((f) => f.role === 'title' || f.role === 'content')),
    )
}

/** 映射的确定性指纹：JSON 规范化后哈希（与后端 canonicalize 语义一致） */
export function mappingHash(mapping: MappingDefinition): string {
  const canonical = JSON.stringify(mapping, Object.keys(mapping).sort())
  let hash = 0
  for (let i = 0; i < canonical.length; i++) {
    hash = (hash << 5) - hash + canonical.charCodeAt(i)
    hash |= 0
  }
  return String(hash >>> 0).padStart(10, '0')
}

export function useJsonMappingWizard(kbId: number) {
  const state = ref<WizardState>({ step: 'upload', files: [] })
  const busy = ref<BusyOperation | null>(null)
  const ingested = ref<{ docId: number; jobId: number } | null>(null)
  const lastError = ref<string | null>(null)
  /** 最近一次结构探查结果：relations 步骤返回 fields 时需要 */
  let profileCache: SourceProfile | null = null
  /** 「继续配置」模式：预置的已上传文档（upload 步骤直接探查，无需再选文件） */
  let resumeDocId: number | null = null
  /** 当前 preview 行对应的映射哈希：映射变更后未重新预览则确认失效 */
  const previewHash = ref<string | null>(null)
  /** 批量导入批次：confirm 步骤逐个入库；null 表示单文档模式 */
  const batchDocs = ref<BatchDocEntry[] | null>(null)

  /** 当前步骤编号（1..6） */
  const stepNumberValue = computed(() => stepNumber(state.value.step))

  const api = {
    profileJson,
    previewJson,
    startJsonIngest,
  }

  async function run<T>(op: BusyOperation, fn: () => Promise<T>): Promise<T> {
    if (busy.value !== null) {
      throw new Error(`操作正在进行中（${busy.value}）`)
    }
    busy.value = op
    lastError.value = null
    try {
      return await fn()
    } finally {
      busy.value = null
    }
  }

  function setFiles(files: File[]) {
    if (busy.value !== null) return
    resumeDocId = null
    state.value = { step: 'upload', files }
  }

  /** 预置批量导入批次（批量模式；单文件模式保持 null） */
  function setBatchDocs(entries: BatchDocEntry[]): void {
    if (busy.value !== null) return
    batchDocs.value = entries
  }

  /** 1→2：探查结构。支持从文档列表「继续配置」进入（无文件但 docId 已存在） */
  async function profile(docId: number): Promise<SourceProfile> {
    const s = state.value
    if (s.step !== 'upload') {
      throw new Error(`当前步骤（${s.step}）不能开始结构探查`)
    }
    if (s.files.length === 0 && resumeDocId !== docId) {
      throw new Error('请先选择要上传的 JSON 文件')
    }
    const result = await run('profile', () => api.profileJson(kbId, docId))
    profileCache = result
    resumeDocId = null
    state.value = { step: 'structure', docId, profile: result }
    return result
  }

  /** 2→3：选择候选记录路径并采用其确定性草稿映射 */
  function selectCandidate(profile: SourceProfile, mapping: MappingDefinition) {
    profileCache = profile
    state.value = { step: 'fields', docId: profile.doc_id, profile, mapping }
  }

  /** 3→4：字段角色/转换确认后进入父子与关系编辑 */
  function nextToRelations(mapping: MappingDefinition): void {
    const s = state.value
    if (s.step !== 'fields') throw new Error(`当前步骤（${s.step}）不能进入关系编辑`)
    if (!hasRequiredIndexedFields(mapping)) {
      throw new Error('每个索引记录类型至少需要一个 title 或 content 字段才能继续')
    }
    state.value = { step: 'relations', docId: s.docId, mapping }
  }

  /** 4→5：请求后端真实预览 */
  async function preview(mapping: MappingDefinition, limit?: number): Promise<PreviewResponse> {
    const s = state.value
    if (s.step !== 'relations') {
      throw new Error(`当前步骤（${s.step}）不能请求预览：请先完成结构探查与映射编辑`)
    }
    const resp = await run('preview', () => api.previewJson(kbId, s.docId, mapping, limit))
    previewHash.value = mappingHash(mapping)
    state.value = { step: 'preview', docId: s.docId, mapping, rows: resp.rows }
    return resp
  }

  /** 预览中映射被编辑：整体替换（原地刷新）；映射变更后确认失效直至重新预览 */
  function applyMappingUpdate(mapping: MappingDefinition): void {
    const s = state.value
    if (s.step !== 'preview') return
    state.value = { step: 'preview', docId: s.docId, mapping, rows: s.rows }
    previewHash.value = null
  }

  /** fields/relations 步骤的整体映射替换（保持 profile 供返回步骤使用） */
  function setStateMapping(mapping: MappingDefinition): void {
    const s = state.value
    if (s.step === 'fields') {
      state.value = { step: 'fields', docId: s.docId, profile: s.profile, mapping }
      return
    }
    if (s.step === 'relations') {
      state.value = { step: 'relations', docId: s.docId, mapping }
      return
    }
    throw new Error(`当前步骤（${s.step}）不能直接替换映射`)
  }

  /** 5→6：仅当预览行与当前映射一致（未发生破坏性变更）才允许进入确认 */
  function next() {
    const s = state.value
    if (s.step === 'fields') {
      nextToRelations(s.mapping)
      return
    }
    if (s.step === 'preview') {
      if (previewHash.value !== mappingHash(s.mapping)) {
        throw new Error('映射已变更，请重新预览后再确认')
      }
      state.value = { step: 'confirm', docId: s.docId, mapping: s.mapping, rows: s.rows }
      return
    }
    throw new Error(`当前步骤（${s.step}）不支持下一步`)
  }

  /** 回到上一步（常驻返回按钮）；丢弃后续步骤的 transient 数据 */
  function back() {
    if (busy.value !== null) return
    const s = state.value
    switch (s.step) {
      case 'structure':
        state.value = { step: 'upload', files: [] }
        return
      case 'fields':
        state.value = { step: 'structure', docId: s.docId, profile: s.profile }
        return
      case 'relations':
        // relations 状态不携带 profile（见 WizardState 联合）；从缓存恢复
        if (profileCache !== null) {
          state.value = {
            step: 'fields',
            docId: s.docId,
            profile: profileCache,
            mapping: s.mapping,
          }
        }
        return
      case 'preview':
        state.value = { step: 'relations', docId: s.docId, mapping: s.mapping }
        return
      case 'confirm':
        state.value = { step: 'preview', docId: s.docId, mapping: s.mapping, rows: s.rows }
        return
      default:
        return
    }
  }

  /** 6→终态：逐个调用 ingest；单条失败不阻断批次，全部成功才重置 transient 数据 */
  async function confirmIngest(mappingVersionId: number): Promise<IngestResult[]> {
    const s = state.value
    if (s.step !== 'confirm') {
      throw new Error(`当前步骤（${s.step}）不能开始入库`)
    }
    const targets: BatchDocEntry[] =
      batchDocs.value ?? [
        { docId: s.docId, name: '', status: 'pending', jobId: null, error: null },
      ]
    const results = await run('ingest', async () => {
      const succeeded: IngestResult[] = []
      for (const entry of targets) {
        if (entry.status === 'ingested') continue
        try {
          const result = await api.startJsonIngest(kbId, {
            doc_id: entry.docId,
            mapping_version_id: mappingVersionId,
          })
          entry.status = 'ingested'
          entry.jobId = result.job_id
          entry.error = null
          succeeded.push(result)
        } catch (err) {
          entry.status = 'failed'
          entry.error = err instanceof Error ? err.message : String(err)
        }
      }
      return succeeded
    })

    if (results.length > 0) {
      const last = results[results.length - 1]
      ingested.value = { docId: last.doc_id, jobId: last.job_id }
    }

    if (targets.every((entry) => entry.status === 'ingested')) {
      profileCache = null
      previewHash.value = null
      resumeDocId = null
      batchDocs.value = null
      // 重置 transient 数据，回到初始 upload 步骤
      state.value = { step: 'upload', files: [] }
    }
    return results
  }

  /** 预置要配置的文档：进入 upload 步骤并允许直接探查已上传文档 */
  function setDocForResume(docId: number): void {
    if (busy.value !== null) return
    state.value = { step: 'upload', files: [] }
    resumeDocId = docId
  }

  return {
    state,
    busy,
    ingested,
    previewHash,
    lastError,
    stepNumber: stepNumberValue,
    api,
    setFiles,
    setBatchDocs,
    batchDocs,
    setDocForResume,
    profile,
    selectCandidate,
    next,
    back,
    preview,
    applyMappingUpdate,
    setStateMapping,
    confirmIngest,
  }
}
