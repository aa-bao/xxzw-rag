import { api } from './client'
import { applicationUrl } from '../platform'
import type {
  Compatibility,
  CreateMappingTemplateVersionInput,
  CreateMappingTemplateVersionResult,
  IngestResult,
  MappingErrorsResponse,
  MappingTemplateSummary,
  MappingDefinition,
  PreviewResponse,
  SourceProfile,
  StartJsonIngestInput,
} from '../types/structured'

/**
 * 结构化 JSON 入库 API。
 * 端点契约（后端 p1a/p3a 批次定稿）：
 *   POST /api/kb/{kb_id}/json/profile
 *   POST /api/kb/{kb_id}/json/preview
 *   GET|POST /api/mapping-templates
 *   POST /api/kb/{kb_id}/json/ingest
 *   GET  /api/docs/{doc_id}/mapping-errors
 */

export async function profileJson(kbId: number, docId: number): Promise<SourceProfile> {
  return (await api.post<SourceProfile>(`/kb/${kbId}/json/profile`, { doc_id: docId })).data
}

export async function previewJson(
  kbId: number,
  docId: number,
  mapping: MappingDefinition,
  limit?: number,
): Promise<PreviewResponse> {
  return (
    await api.post<PreviewResponse>(`/kb/${kbId}/json/preview`, {
      doc_id: docId,
      mapping,
      limit,
    })
  ).data
}

/** 解析后端 JSON 预览错误：返回 { message, source_pointer? }（前端不重建转换） */
export function previewErrorMessage(err: unknown): { message: string; source_pointer: string | null } {
  if (err && typeof err === 'object') {
    const e = err as { error?: { code?: string; message?: string; source_pointer?: string | null } }
    const message = e.error?.message ?? e.error?.code ?? ''
    const pointer = e.error?.source_pointer ?? null
    if (message) return { message, source_pointer: pointer }
  }
  return { message: '预览失败，请重试', source_pointer: null }
}

export async function listMappingTemplates(): Promise<MappingTemplateSummary[]> {
  return (await api.get<MappingTemplateSummary[]>('/mapping-templates')).data
}

export async function createMappingTemplateVersion(
  input: CreateMappingTemplateVersionInput,
): Promise<CreateMappingTemplateVersionResult> {
  return (await api.post<CreateMappingTemplateVersionResult>('/mapping-templates', input)).data
}

export async function startJsonIngest(kbId: number, input: StartJsonIngestInput): Promise<IngestResult> {
  return (await api.post<IngestResult>(`/kb/${kbId}/json/ingest`, input)).data
}

export async function fetchMappingErrors(docId: number): Promise<MappingErrorsResponse> {
  return (await api.get<MappingErrorsResponse>(`/docs/${docId}/mapping-errors`)).data
}

/** 逐记录错误下载链接（浏览器直接下载） */
export function mappingErrorsUrl(docId: number): string {
  return applicationUrl(`api/docs/${docId}/mapping-errors/download`)
}

/** 在映射变化时对模板做兼容性检查（后端兼容性判定） */
export async function checkMappingCompatibility(
  kbId: number,
  docId: number,
  mapping: MappingDefinition,
): Promise<Compatibility> {
  return (
    await api.post<Compatibility>(`/kb/${kbId}/json/preview`, {
      doc_id: docId,
      mapping,
      compatibility_only: true,
    })
  ).data
}
