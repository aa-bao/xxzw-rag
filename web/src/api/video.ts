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
  frames_requested?: number
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
}

/** 视频数据库：历史任务列表 */
export async function listLibrary(): Promise<LibraryTask[]> {
  const resp = await client.get<LibraryTask[]>('/video/library')
  return resp.data
}

/** 历史任务关键帧图片 URL */
export function libraryFrameUrl(taskDir: string, name: string): string {
  return applicationUrl('/api/video/library/' + encodeURIComponent(taskDir) + '/frames/' + name)
}

/** 历史任务 HTML 报告 URL */
export function libraryReportUrl(taskDir: string): string {
  return applicationUrl('/api/video/library/' + encodeURIComponent(taskDir) + '/report.html')
}

// ── 系统设置 ──

export interface VideoEnvInfo {
  script?: string
  python?: string
  library_root?: string
  output_root?: string
  task_root?: string
  upload_dir?: string
  dashscope_ready?: boolean
}

export interface VideoPrefs {
  frames: number
  qa_model: string
}

/** 视频解析运行环境信息 */
export async function getVideoEnv(): Promise<VideoEnvInfo> {
  const resp = await client.get<VideoEnvInfo>('/video/env')
  return resp.data
}

/** 读取解析偏好 */
export async function getVideoPrefs(): Promise<VideoPrefs> {
  const resp = await client.get<VideoPrefs>('/video/prefs')
  return resp.data
}

/** 保存解析偏好 */
export async function saveVideoPrefs(prefs: VideoPrefs): Promise<void> {
  await client.put('/video/prefs', prefs)
}

/** 关键帧图片 URL */
export function frameUrl(taskId: string, name: string): string {
  return applicationUrl('/api/video/tasks/' + taskId + '/frames/' + name)
}

/** HTML 报告 URL */
export function reportUrl(taskId: string): string {
  return applicationUrl('/api/video/tasks/' + taskId + '/report.html')
}

/** 基于转录问答 */
export async function askQuestion(taskId: string, question: string): Promise<{ answer: string }> {
  const resp = await client.post<{ answer: string }>('/video/tasks/' + taskId + '/qa', { question })
  return resp.data
}
