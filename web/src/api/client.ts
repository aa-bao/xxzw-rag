import type { Kb } from '../types/kb'
import { applicationUrl, redirectToSessionExpiry } from '../platform'

export interface ApiResponse<T = unknown> {
  success: boolean
  data: T
  error?: { code: string; message: string }
}

class Client {
  private endpoint(url: string): string {
    return applicationUrl(`api${url}`)
  }

  /**
   * 会话过期（401）统一处理：嵌入模式回主系统，独立部署回 /login。
   * 登录接口自身的 401（密码错误）不触发跳转；不向 URL 写入任何错误信息或 token。
   */
  private redirectOnUnauthorized(requestUrl: string): void {
    if (requestUrl.includes('/auth/login')) return
    redirectToSessionExpiry()
  }

  private async request<T>(url: string, init?: RequestInit): Promise<ApiResponse<T>> {
    const resp = await fetch(this.endpoint(url), {
      ...init,
      credentials: 'same-origin' as RequestCredentials,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
    if (resp.status === 401) this.redirectOnUnauthorized(url)
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

  patch<T>(url: string, data?: unknown) {
    return this.request<T>(url, {
      method: 'PATCH',
      body: data === undefined ? undefined : JSON.stringify(data),
    })
  }

  delete<T>(url: string) {
    return this.request<T>(url, { method: 'DELETE' })
  }

  async upload<T>(url: string, form: FormData): Promise<ApiResponse<T>> {
    const resp = await fetch(this.endpoint(url), {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    if (resp.status === 401) this.redirectOnUnauthorized(url)
    const body = await resp.json()
    if (!resp.ok) {
      throw body
    }
    return body
  }

  /** SSE 查询：返回原始 Response，由调用方用 streamSse 消费 */
  async queryRaw(url: string, body: unknown): Promise<Response> {
    const resp = await fetch(this.endpoint(url), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (resp.status === 401) this.redirectOnUnauthorized(url)
    return resp
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
    const resp = await fetch(this.endpoint(`/docs/upload?kb_id=${kbId}`), {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    if (resp.status === 401) this.redirectOnUnauthorized(`/docs/upload`)
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
    return fetch(this.endpoint('/chat/query'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId, question }),
    }).then((resp) => {
      if (resp.status === 401) this.redirectOnUnauthorized('/chat/query')
      return resp
    })
  }
}

export const api = new Client()
