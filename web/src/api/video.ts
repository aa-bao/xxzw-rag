import { api as client } from './client'
import { applicationUrl } from '../platform'

/** 视频解析任务状态 */
export interface VideoTask {
  task_id: string
  source: string
  kind: 'url' | 'file'
  status: 'submitted' | 'running' | 'complete' | 'failed'
  created_at: string
  updated_at: string
  output_dir: string | null
  error: string | null
  stage: string | null
  report: Record<string, unknown> | null
  transcript: string | null
  keyframes: Array<{ path: string; timestamp_seconds: number }>
  cost: Record<string, unknown> | null
  summary: VideoSummary | null
  frames_requested?: number
  transcript_source?: string | null
  events?: VideoPipelineEvent[]
  qa_history?: VideoQaMessage[]
}

/** 流水线事件（SSE / 状态文件均使用） */
export interface VideoPipelineEvent {
  seq: number
  time: string
  stage: string
  title: string
  message: string
  level: 'info' | 'success' | 'warning' | 'error'
  data?: Record<string, unknown>
}

/** 视频问答消息 */
export interface VideoQaMessage {
  role: 'user' | 'assistant'
  content: string
  time?: string
}

/** 视频摘要（summary.json） */
export interface VideoSummary {
  title?: string
  summary?: string
  keypoints?: string[]
  visual_notes?: string[]
  keyframe_captions?: Record<string, string>
  mode?: string
}

export interface SubmitResult {
  task_id: string
  status: string
}

/** 提交 URL 解析任务 */
export async function submitUrlTask(source: string, frames = 12): Promise<VideoTask> {
  const resp = await client.post<VideoTask>('/video/tasks', { source, kind: 'url', frames })
  return resp.data
}

/** 上传本地视频文件，返回暂存路径 */
export async function uploadVideoFile(file: File): Promise<{ path: string; size: number; filename: string }> {
  const form = new FormData()
  form.append('file', file)
  const resp = await client.upload<{ path: string; size: number; filename: string }>('/video/tasks/upload', form)
  return resp.data
}

/** 提交本地文件解析任务 */
export async function submitFileTask(path: string, frames = 12): Promise<VideoTask> {
  const resp = await client.post<VideoTask>('/video/tasks', { source: path, kind: 'file', frames })
  return resp.data
}

/** 查询任务状态 */
export async function getTask(taskId: string): Promise<VideoTask> {
  const resp = await client.get<VideoTask>('/video/tasks/' + taskId)
  return resp.data
}

/** 任务列表 */
export async function listTasks(): Promise<VideoTask[]> {
  const resp = await client.get<VideoTask[]>('/video/tasks')
  return resp.data
}

/** 删除任务 */
export async function deleteTask(taskId: string): Promise<void> {
  await client.delete('/video/tasks/' + taskId)
}

// ── 视频数据库（历史任务） ──

export interface LibraryTask {
  task_id: string
  output_dir: string
  title: string
  summary: string
  keypoints: string[]
  transcript: string
  keyframes: Array<{ path: string; timestamp_seconds: number }>
  has_report: boolean
  created_at: string | null
  source: string
  duration_seconds: number | null
  cost: Record<string, unknown> | null
  transcript_source?: string
  visual_notes?: string[]
  /** 来源分类：weixin | bilibili | douyin | youtube | local | other */
  source_kind?: string
}

/** 视频数据库：历史任务列表 */
export async function listLibrary(): Promise<LibraryTask[]> {
  const resp = await client.get<LibraryTask[]>('/video/library')
  return resp.data
}

/** 删除视频数据库中的单个历史任务（目录被永久删除，不可恢复） */
export async function deleteLibraryTask(taskDir: string): Promise<void> {
  await client.delete('/video/library/' + encodeURIComponent(taskDir))
}

/** 历史任务关键帧图片 URL */
export function libraryFrameUrl(taskDir: string, name: string): string {
  return applicationUrl('/api/video/library/' + encodeURIComponent(taskDir) + '/frames/' + name)
}

/** 历史任务 HTML 报告 URL */
export function libraryReportUrl(taskDir: string): string {
  return applicationUrl('/api/video/library/' + encodeURIComponent(taskDir) + '/report.html')
}

// ── agent设置 ──

export interface VideoEnvInfo {
  pipeline?: string
  python?: string
  library_root?: string
  output_root?: string
  task_root?: string
  upload_dir?: string
  asr_configured?: boolean
}

export interface VideoSettings {
  asr_provider: string
  asr_model: string
  asr_resource_id?: string
  has_asr_api_key: boolean
  has_asr_app_id: boolean
  has_asr_access_token: boolean
  chat_base_url: string
  chat_model: string
  has_chat_api_key: boolean
  qa_model: string
  qa_base_url: string
  has_qa_api_key: boolean
  frames: number
}

export interface VideoSettingsUpdate {
  asr_model?: string
  asr_api_key?: string
  asr_app_id?: string
  asr_access_token?: string
  chat_base_url?: string
  chat_model?: string
  chat_api_key?: string
  qa_model?: string
  qa_base_url?: string
  qa_api_key?: string
  frames?: number
}

export interface VideoSettingsTestResult {
  ok: boolean
  message?: string
}

export interface VideoSettingsTestRequest {
  mode: 'asr' | 'chat' | 'chat_summary' | 'chat_qa'
  asr_model?: string
  asr_api_key?: string
  asr_app_id?: string
  asr_access_token?: string
  chat_base_url?: string
  chat_model?: string
  chat_api_key?: string
  qa_model?: string
  qa_base_url?: string
  qa_api_key?: string
}

/** 视频解析运行环境信息 */
export async function getVideoEnv(): Promise<VideoEnvInfo> {
  const resp = await client.get<VideoEnvInfo>('/video/env')
  return resp.data
}

/** 读取视频 agent 设置 */
export async function getVideoSettings(): Promise<VideoSettings> {
  const resp = await client.get<VideoSettings>('/video/settings')
  return resp.data
}

/** 更新视频 agent 设置 */
export async function updateVideoSettings(settings: VideoSettingsUpdate): Promise<VideoSettings> {
  const resp = await client.put<VideoSettings>('/video/settings', settings)
  return resp.data
}

/** 测试连接（asr / chat） */
export async function testVideoSettings(body: VideoSettingsTestRequest): Promise<VideoSettingsTestResult> {
  const resp = await client.post<VideoSettingsTestResult>('/video/settings/test', body)
  return resp.data
}

/** 关键帧图片 URL */
export function frameUrl(taskId: string, name: string): string {
  return applicationUrl('/api/video/tasks/' + taskId + '/frames/' + name)
}

/** HTML 报告 URL */
export function reportUrl(taskId: string): string {
  return applicationUrl('/api/video/tasks/' + taskId + '/report.html')
}

/** 视频 Agent 头像 URL（后端静态资源） */
export function agentAvatarUrl(): string {
  return applicationUrl('/api/video/agent-avatar')
}

/** 基于转录/画面问答 */
export async function askQuestion(taskId: string, question: string): Promise<{ answer: string }> {
  const resp = await client.post<{ answer: string }>('/video/tasks/' + taskId + '/qa', { question })
  return resp.data
}

/** 读取某任务的问答历史 */
export async function getQaHistory(taskId: string): Promise<VideoQaMessage[]> {
  const resp = await client.get<VideoQaMessage[]>('/video/tasks/' + taskId + '/qa/history')
  return resp.data
}

/** SSE 事件流 URL（EventSource 直接连接） */
export function taskEventUrl(taskId: string): string {
  return applicationUrl('/api/video/tasks/' + taskId + '/events')
}

/** SSE 流式问答：返回原始 Response，用 streamSse 消费 */
export function askQuestionStream(taskId: string, question: string): Promise<Response> {
  return client.queryRaw('/video/tasks/' + taskId + '/qa/stream', { question })
}
