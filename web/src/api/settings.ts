import { api } from './client'

export interface ModelSettings {
  base_url: string
  chat_model: string
  embedding_model: string
  /** 空字符串 = 与 chat 共用 base_url */
  embedding_base_url: string | null
  has_chat_api_key: boolean
  has_embedding_api_key: boolean
}

export interface UpdateModelSettings {
  base_url?: string
  chat_model?: string
  embedding_model?: string
  /** null = 与 chat 共用；空字符串=不修改；仅传 string 时生效 */
  embedding_base_url?: string | null
  /** 空字符串 = 不更新（保留原值） */
  api_key?: string
  /** 空字符串 = 不更新（保留原值） */
  embedding_api_key?: string
}

export type TestModelMode = 'chat' | 'embedding'

export interface TestModelSettingsInput {
  mode: TestModelMode
  base_url: string
  chat_model?: string
  api_key?: string
  embedding_base_url?: string | null
  embedding_model?: string
  embedding_api_key?: string
}

export interface TestModelSettingsResult {
  ok: boolean
  /** 失败时的错误信息（如 MODEL_AUTH_FAILED / 网络不通） */
  message?: string
}

export async function getModelSettings(): Promise<ModelSettings> {
  const resp = await api.get<ModelSettings>('/settings/models')
  return resp.data
}

export async function updateModelSettings(input: UpdateModelSettings): Promise<ModelSettings> {
  const resp = await api.put<ModelSettings>('/settings/models', input)
  return resp.data
}

/** 用给定配置试连 embedding 服务，验证模型可用 */
export async function testModelSettings(input: TestModelSettingsInput): Promise<TestModelSettingsResult> {
  const resp = await api.post<TestModelSettingsResult>('/settings/models/test', input)
  return resp.data
}
