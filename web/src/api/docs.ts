import { api } from './client'

/** 文档解析状态：pending 排队 / running 解析中 / done 完成 / failed 失败 / deleting 删除中 */
export type DocStatus = 'pending' | 'running' | 'done' | 'failed' | 'deleting'

/** 解析阶段（job 粒度）：parsing 解析文本 / chunking 分块 / embedding 向量化 / indexing 写入索引 */
export type DocStage = 'parsing' | 'chunking' | 'embedding' | 'indexing'

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
  job_id: number
  status: string
}

export interface DocStatusDetail {
  doc_id: number
  status: DocStatus
  stage: DocStage | null
  chunk_count: number
  error_message: string | null
}

/** 文档状态到前端展示态的归一化：兼容历史枚举（processing/completed/success/error） */
export type DocDisplayStatus = 'pending' | 'running' | 'done' | 'failed' | 'deleting'

export function normalizeDocStatus(status: string | null | undefined): DocDisplayStatus {
  const key = (status ?? 'pending').toLowerCase()
  const map: Record<string, DocDisplayStatus> = {
    pending: 'pending',
    queued: 'pending',
    running: 'running',
    processing: 'running',
    done: 'done',
    completed: 'done',
    success: 'done',
    failed: 'failed',
    error: 'failed',
    deleting: 'deleting',
  }
  return map[key] ?? 'pending'
}

export async function listDocs(kbId: number): Promise<DocInfo[]> {
  const resp = await api.get<DocInfo[]>(`/kb/${kbId}/docs`)
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
