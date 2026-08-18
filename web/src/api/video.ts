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
