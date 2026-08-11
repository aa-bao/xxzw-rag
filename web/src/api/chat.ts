import { api } from './client'

export interface ConversationInfo {
  id: string
  kb_ids: number[] // 会话绑定的知识库集合
  kb_names: string[] // 与 kb_ids 顺序对应的知识库名
  kb_id: number | null // 兼容字段 = kb_ids[0]
  kb_name?: string // 兼容字段
  title: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ReferenceInfo {
  chunk_id: string | null
  doc_id: number | null
  title: string | null
  snippet: string | null
  score: number | null
  page: number | null
  kb_id?: number | null
  kb_name?: string | null
  is_neighbor?: boolean
}

export interface ChatMessageInfo {
  role: 'user' | 'assistant'
  content: string
  status: 'completed' | 'failed'
  created_at: string | null
  references: ReferenceInfo[]
}

export async function createConversation(kbIds: number[]): Promise<{ id: string; kb_ids: number[]; kb_id: number }> {
  const resp = await api.post<{ id: string; kb_ids: number[]; kb_id: number }>('/chat/conversations', {
    kb_ids: kbIds,
  })
  return resp.data
}

export async function listConversations(): Promise<ConversationInfo[]> {
  const resp = await api.get<ConversationInfo[]>('/chat/history')
  return resp.data
}

export async function deleteConversation(id: string): Promise<void> {
  await api.delete<null>(`/chat/conversations/${id}`)
}

export async function renameConversation(
  id: string,
  title: string,
): Promise<{ id: string; title: string; updated_at: string | null }> {
  const resp = await api.patch<{ id: string; title: string; updated_at: string | null }>(
    `/chat/conversations/${id}`,
    { title },
  )
  return resp.data
}

export async function getMessages(conversationId: string): Promise<ChatMessageInfo[]> {
  const resp = await api.get<ChatMessageInfo[]>(`/chat/conversations/${conversationId}/messages`)
  return resp.data
}

/** SSE 流式问答：返回原始 Response，用 streamSse 消费 */
export function queryRaw(conversationId: string, question: string): Promise<Response> {
  return api.queryRaw('/chat/query', { conversation_id: conversationId, question })
}
