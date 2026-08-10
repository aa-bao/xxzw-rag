import { api } from './client'

/** 索引状态：ready 就绪 / rebuilding 重建中 / failed 失败 / deleting 删除中 */
export type KbIndexStatus = 'ready' | 'rebuilding' | 'failed' | 'deleting'

export interface KbInfo {
  id: number
  name: string
  description: string | null
  chunk_size: number
  overlap: number
  embedding_model: string
  embedding_dimension: number
  active_collection: string
  index_version: number
  index_status: KbIndexStatus
  similarity_threshold: number
  top_k: number
  /** 封面图 URL（未设置封面时为 null） */
  cover_url: string | null
  created_at: string | null
  updated_at: string | null
  /** 文档数（列表统计，后端补字段） */
  doc_count?: number
  /** chunk 总数（列表统计，后端补字段） */
  chunk_total?: number
}

export interface CreateKbInput {
  name: string
  description?: string
  chunk_size?: number
  overlap?: number
}

export interface UpdateKbInput {
  name?: string
  description?: string
  chunk_size?: number
  overlap?: number
  similarity_threshold?: number
  top_k?: number
}

export async function listKbs(): Promise<KbInfo[]> {
  const resp = await api.get<KbInfo[]>('/kb')
  return resp.data
}

export async function getKb(id: number): Promise<KbInfo> {
  const resp = await api.get<KbInfo>(`/kb/${id}`)
  return resp.data
}

export async function createKb(input: CreateKbInput): Promise<KbInfo> {
  const resp = await api.post<KbInfo>('/kb', input)
  return resp.data
}

export async function updateKb(id: number, input: UpdateKbInput): Promise<KbInfo> {
  const resp = await api.put<KbInfo>(`/kb/${id}`, input)
  return resp.data
}

export async function deleteKb(id: number): Promise<void> {
  await api.delete<null>(`/kb/${id}`)
}

/** 上传知识库封面图（jpg/png/webp/gif，≤5MB） */
export async function uploadKbCover(id: number, file: File): Promise<KbInfo> {
  const form = new FormData()
  form.append('file', file)
  const resp = await api.upload<KbInfo>(`/kb/${id}/cover`, form)
  return resp.data
}
