import { api } from './client'

export interface RetrievedChunk {
  chunk_id: string
  content: string
  doc_id: number
  title: string
  page: number | null
  score: number
}

export interface TestRetrievalInput {
  question: string
  top_k?: number
  similarity_threshold?: number
}

export async function testRetrieval(kbId: number, input: TestRetrievalInput): Promise<RetrievedChunk[]> {
  const resp = await api.post<RetrievedChunk[]>(`/kb/${kbId}/test`, input)
  return resp.data
}

export interface ChunkInfo {
  chunk_id: string
  content: string
  doc_id: number
  page: number | null
  score: number | null
  created_at: string | null
}

export interface ChunkPage {
  total: number
  items: ChunkInfo[]
}

export async function listChunks(
  kbId: number,
  docId: number,
  opts?: { page?: number; page_size?: number; keyword?: string },
): Promise<ChunkPage> {
  const params = new URLSearchParams()
  if (opts?.page !== undefined) params.set('page', String(opts.page))
  if (opts?.page_size !== undefined) params.set('page_size', String(opts.page_size))
  if (opts?.keyword) params.set('keyword', opts.keyword)
  const qs = params.toString()
  const resp = await api.get<ChunkPage>(`/kb/${kbId}/docs/${docId}/chunks${qs ? `?${qs}` : ''}`)
  return resp.data
}

export async function updateChunk(
  kbId: number,
  docId: number,
  chunkId: string,
  content: string,
): Promise<ChunkInfo> {
  const encodedChunkId = encodeURIComponent(chunkId)
  const resp = await api.put<ChunkInfo>(
    `/kb/${kbId}/docs/${docId}/chunks/${encodedChunkId}`,
    { content },
  )
  return resp.data
}
