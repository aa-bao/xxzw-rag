import { api } from './client'
import { applicationUrl } from '../platform'

/**
 * 文档状态（镜像后端 rag_document.status 完整枚举）。
 * 结构化 JSON 入库流程：awaiting_mapping → previewing → queued → mapping →
 * chunking → embedding → indexing_lexical → indexing_dense → activating → done。
 */
export type DocStatus =
  | 'pending'
  | 'running'
  | 'uploaded'
  | 'profiling'
  | 'awaiting_mapping'
  | 'previewing'
  | 'queued'
  | 'mapping'
  | 'chunking'
  | 'embedding'
  | 'indexing_lexical'
  | 'indexing_dense'
  | 'activating'
  | 'done'
  | 'done_with_warnings'
  | 'failed'
  | 'deleting'

/** 解析阶段（job 粒度，镜像后端 DocumentJob.stage 枚举） */
export type DocStage =
  | 'parsing'
  | 'indexing'
  | 'profiling'
  | 'awaiting_mapping'
  | 'previewing'
  | 'queued'
  | 'mapping'
  | 'chunking'
  | 'embedding'
  | 'indexing_lexical'
  | 'indexing_dense'
  | 'activating'

export interface DocInfo {
  id: number
  title: string
  source: string
  source_type: string
  status: DocStatus
  chunk_count: number
  file_size_bytes: number | null
  error_message: string | null
  created_at: string | null
  ingested_at: string | null
}

export interface UploadDocResult {
  doc_id: number
  /** 普通文档立即创建任务；JSON/JSONL 等待映射，因此没有 job_id。 */
  job_id?: number
  status: string
}

export interface DocStatusDetail {
  doc_id: number
  status: DocStatus
  stage: DocStage | null
  chunk_count: number
  error_message: string | null
}

export interface DocListEntry extends DocInfo {
  /** 结构化映射是否已确认（仅 awaiting_mapping/failed 结构化文档可能为 true） */
  mapping_ready: boolean
}

/** 文档状态到前端展示态的归一化：兼容历史枚举（processing/completed/success/error） */
export type DocDisplayStatus =
  | 'pending'
  | 'running'
  | 'awaiting_mapping'
  | 'done'
  | 'done_with_warnings'
  | 'failed'
  | 'deleting'

const DISPLAY_MAP: Record<string, DocDisplayStatus> = {
  pending: 'pending',
  queued: 'pending',
  running: 'running',
  processing: 'running',
  uploaded: 'awaiting_mapping',
  profiling: 'running',
  awaiting_mapping: 'awaiting_mapping',
  previewing: 'running',
  mapping: 'running',
  chunking: 'running',
  embedding: 'running',
  indexing_lexical: 'running',
  indexing_dense: 'running',
  activating: 'running',
  done: 'done',
  completed: 'done',
  success: 'done',
  done_with_warnings: 'done_with_warnings',
  failed: 'failed',
  error: 'failed',
  deleting: 'deleting',
}

/** 非终态（需要继续轮询）。
 *  awaiting_mapping 不在此列：该状态没有进行中的入库 job，状态不会自动变化，
 *  轮询只会周期性替换 docs 数组导致表格勾选状态被重置；入库后由向导重新 pollDoc。 */
export function isNonterminal(status: DocDisplayStatus | string): boolean {
  const s = normalizeDocStatus(status)
  return s === 'pending' || s === 'running'
}

export function normalizeDocStatus(status: string | null | undefined): DocDisplayStatus {
  const key = (status ?? 'pending').toLowerCase()
  return DISPLAY_MAP[key] ?? 'pending'
}

export async function listDocs(kbId: number): Promise<DocListEntry[]> {
  const resp = await api.get<DocListEntry[]>(`/kb/${kbId}/docs`)
  return resp.data
}

export async function uploadDoc(kbId: number, file: File): Promise<UploadDocResult> {
  const form = new FormData()
  form.append('file', file)
  const resp = await api.upload<UploadDocResult>(`/kb/${kbId}/docs/upload`, form)
  return resp.data
}

export async function deleteDoc(kbId: number, docId: number): Promise<void> {
  await api.delete<null>(`/kb/${kbId}/docs/${docId}`)
}

/** 重建索引：删除旧 chunk 并重新解析入库 */
export async function reindexDoc(kbId: number, docId: number): Promise<{ job_id: number }> {
  const resp = await api.post<{ job_id: number }>(`/kb/${kbId}/docs/${docId}/reindex`)
  return resp.data
}

export async function docStatus(kbId: number, docId: number): Promise<DocStatusDetail> {
  const resp = await api.get<DocStatusDetail>(`/kb/${kbId}/docs/${docId}/status`)
  return resp.data
}

export interface DocRaw {
  doc_id: number
  title: string
  content: string
  file_size_bytes: number | null
}

/** 读取文档原始文件内容（txt/md） */
export async function getDocRaw(kbId: number, docId: number): Promise<DocRaw> {
  const resp = await api.get<DocRaw>(`/kb/${kbId}/docs/${docId}/raw`)
  return resp.data
}

/** 逐记录映射错误下载链接（浏览器直接下载；仅 mapping_ready 结构化文档存在） */
export function mappingErrorsUrl(docId: number): string {
  return applicationUrl(`api/docs/${docId}/mapping-errors/download`)
}
