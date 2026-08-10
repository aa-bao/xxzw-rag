/**
 * 知识库共享类型（KbListView / KbDetailView / WorkspaceView 共用）
 * 字段对应后端 /api/kb 返回的 KnowledgeBase。
 */
export interface Kb {
  id: number
  name: string
  description: string | null
  chunk_size: number
  overlap: number
  embedding_model: string
  embedding_dimension: number
  active_collection: string
  index_version: number
  index_status: string
  similarity_threshold: number
  top_k: number
  created_at: string
  updated_at: string
}
