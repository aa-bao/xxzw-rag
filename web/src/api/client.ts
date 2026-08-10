import type { Kb } from '../types/kb'

export interface ApiResponse<T = unknown> {
  success: boolean
  data: T
  error?: { code: string; message: string }
}

class Client {
  private base = '/api'

  private async request<T>(url: string, init?: RequestInit): Promise<ApiResponse<T>> {
    const resp = await fetch(`${this.base}${url}`, {
      ...init,
      credentials: 'same-origin' as RequestCredentials,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
    const body = await resp.json()
    if (!resp.ok) {
      throw body
    }
    return body
  }

  get<T>(url: string) {
    return this.request<T>(url)
  }

  post<T>(url: string, data?: unknown) {
    return this.request<T>(url, {
      method: 'POST',
      body: data === undefined ? undefined : JSON.stringify(data),
    })
  }

  put<T>(url: string, data?: unknown) {
    return this.request<T>(url, {
      method: 'PUT',
      body: data === undefined ? undefined : JSON.stringify(data),
    })
  }

  delete<T>(url: string) {
    return this.request<T>(url, { method: 'DELETE' })
  }

  async upload<T>(url: string, form: FormData): Promise<ApiResponse<T>> {
    const resp = await fetch(`${this.base}${url}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    const body = await resp.json()
    if (!resp.ok) {
      throw body
    }
    return body
  }

  /** SSE 查询：返回原始 Response，由调用方用 streamSse 消费 */
  async queryRaw(url: string, body: unknown): Promise<Response> {
    return fetch(`${this.base}${url}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  }

  async login(username: string, password: string) {
    return this.request<{ id: number; username: string; role: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  }

  async logout() {
    return this.request<null>('/auth/logout', { method: 'POST' })
  }

  async me() {
    return this.request<{ id: number; username: string; role: string }>('/auth/me')
  }

  async listKbs() {
    return this.request<Array<Kb>>('/kb')
  }

  async createKb(name: string, description?: string) {
    return this.request<{ id: number }>('/kb', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    })
  }

  async uploadDoc(kbId: number, file: File) {
    const form = new FormData()
    form.append('file', file)
    const resp = await fetch(`${this.base}/docs/upload?kb_id=${kbId}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    return resp.json() as Promise<ApiResponse<{ doc_id: number; job_id: number; status: string }>>
  }

  async docStatus(docId: number) {
    return this.request<{ doc_id: number; status: string; chunk_count: number }>(`/docs/${docId}/status`)
  }

  async createConversation(kbId: number) {
    return this.request<{ id: string }>('/chat/conversations', {
      method: 'POST',
      body: JSON.stringify({ kb_id: kbId }),
    })
  }

  async listConversations() {
    return this.request<Array<{ id: string; kb_id: number; title: string | null }>>('/chat/history')
  }

  query(conversationId: string, question: string) {
    return fetch(`${this.base}/chat/query`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId, question }),
    })
  }
}

export const api = new Client()
