import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import TemplateImportDialog from './TemplateImportDialog.vue'
import type { MappingDefinition, MappingTemplateSummary } from '../types/structured'

vi.mock('../api/kb', () => ({
  getKb: vi.fn(),
  setDefaultMappingVersion: vi.fn(),
  listKbs: vi.fn().mockResolvedValue([]),
  createKb: vi.fn(),
  updateKb: vi.fn(),
  deleteKb: vi.fn(),
  uploadKbCover: vi.fn(),
}))

vi.mock('../api/docs', () => ({
  uploadDoc: vi.fn(),
  listDocs: vi.fn().mockResolvedValue([]),
  deleteDoc: vi.fn(),
  reindexDoc: vi.fn(),
  docStatus: vi.fn(),
  getDocRaw: vi.fn(),
}))

vi.mock('../api/structured', () => ({
  listMappingTemplates: vi.fn(),
  previewJson: vi.fn(),
  startJsonIngest: vi.fn(),
  profileJson: vi.fn(),
  createMappingTemplateVersion: vi.fn(),
  checkMappingCompatibility: vi.fn(),
}))

const { getKb } = await import('../api/kb')
const { deleteDoc, uploadDoc } = await import('../api/docs')
const { listMappingTemplates, previewJson, startJsonIngest } = await import('../api/structured')

const mapping: MappingDefinition = {
  source_format: 'jsonl',
  record_types: [
    {
      name: 'qa_unit',
      record_path: '$',
      fields: [],
      children: [],
      chunk_policy: 'atomic',
      relation_rule: null,
    },
  ],
}

const template: MappingTemplateSummary = {
  id: 9,
  name: '仲帅-QA单元-atomic-v1',
  source_format: 'jsonl',
  current_version: 1,
  fingerprint: 'fp',
  usage_count: 0,
  created_at: null,
  updated_at: null,
  versions: [
    { id: 14, version: 1, mapping, fingerprint: 'fp', usage_count: 0, created_at: null, created_by: null },
  ],
}

function dialogScope(_wrapper: VueWrapper): Element {
  // 组件使用 append-to-body，dialog 内容渲染在 document.body
  return document.body
}

function queryFileInput(scope: Element): HTMLInputElement | null {
  return scope.querySelector<HTMLInputElement>('input[type="file"]')
}

function buttonsByText(scope: Element, text: string): HTMLButtonElement[] {
  return [...scope.querySelectorAll<HTMLButtonElement>('button')].filter((b) =>
    b.textContent?.includes(text),
  )
}

async function pickFile(scope: Element, file: File) {
  const input = queryFileInput(scope)
  if (!input) throw new Error('no file input found')
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getKb).mockResolvedValue({
    id: 5,
    name: 'Test KB',
    default_mapping_version_id: 14,
  } as never)
  vi.mocked(listMappingTemplates).mockResolvedValue([template])
  vi.mocked(uploadDoc).mockResolvedValue({ doc_id: 100, job_id: 1, status: 'awaiting_mapping' })
  vi.mocked(previewJson).mockResolvedValue({ rows: [], warnings: [], total_rows: 3, limit: 1 })
  vi.mocked(startJsonIngest).mockResolvedValue({
    doc_id: 100,
    job_id: 2,
    mapping_version_id: 14,
    status: 'queued',
  })
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('TemplateImportDialog', () => {
  it('uploads, selects the KB default template, previews record count and ingests', async () => {
    const wrapper = mount(TemplateImportDialog, {
      props: { visible: true, kbId: 5 },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const scope = dialogScope(wrapper)
    await pickFile(scope, new File(['{"id":"a"}'], 'qa.jsonl', { type: 'application/jsonl' }))

    await buttonsByText(scope, '上传文件')[0]?.click()
    await flushPromises()

    expect(vi.mocked(uploadDoc)).toHaveBeenCalledWith(5, expect.any(File))
    expect(scope.textContent).toContain('仲帅-QA单元-atomic-v1')

    await buttonsByText(scope, '预览记录数')[0]?.click()
    await flushPromises()

    expect(vi.mocked(previewJson)).toHaveBeenCalledWith(5, 100, mapping, 1)
    expect(scope.textContent).toContain('3')

    await buttonsByText(scope, '确认入库')[0]?.click()
    await flushPromises()

    expect(vi.mocked(startJsonIngest)).toHaveBeenCalledWith(5, { doc_id: 100, mapping_version_id: 14 })
    const ingested = wrapper.emitted('ingested')
    expect(ingested).toBeTruthy()
    expect(ingested?.[0]?.[0]).toMatchObject({ docId: 100, jobId: 2, name: 'qa.jsonl' })
  })

  it('shows a message when no matching template exists', async () => {
    vi.mocked(listMappingTemplates).mockResolvedValue([])
    const wrapper = mount(TemplateImportDialog, {
      props: { visible: true, kbId: 5 },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const scope = dialogScope(wrapper)
    await pickFile(scope, new File(['{}'], 'data.jsonl', { type: 'application/jsonl' }))
    await buttonsByText(scope, '上传文件')[0]?.click()
    await flushPromises()

    expect(scope.textContent).toContain('没有可用的 jsonl 模板')
  })

  it('skips files with zero preview rows and deletes their temporary uploads', async () => {
    vi.mocked(previewJson).mockResolvedValue({ rows: [], warnings: [], total_rows: 0, limit: 1 })
    const wrapper = mount(TemplateImportDialog, {
      props: { visible: true, kbId: 5 },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const scope = dialogScope(wrapper)
    await pickFile(scope, new File(['{}'], 'empty.jsonl', { type: 'application/jsonl' }))
    await buttonsByText(scope, '上传文件')[0]?.click()
    await flushPromises()
    await buttonsByText(scope, '预览记录数')[0]?.click()
    await flushPromises()

    expect(scope.textContent).toContain('没有可导入的 QA 单元')

    await buttonsByText(scope, '确认入库')[0]?.click()
    await flushPromises()

    expect(vi.mocked(deleteDoc)).toHaveBeenCalledWith(5, 100)
    expect(vi.mocked(startJsonIngest)).not.toHaveBeenCalled()
  })
})
