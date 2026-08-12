import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'
import KbDocsView from '../../views/KbDocsView.vue'
import JsonMappingWizard from './JsonMappingWizard.vue'
import UploadDialog from '../UploadDialog.vue'
import UploadTypeDialog from '../UploadTypeDialog.vue'

vi.mock('../../api/structured', () => ({
  profileJson: vi.fn(),
  previewJson: vi.fn(),
  listMappingTemplates: vi.fn().mockResolvedValue([]),
  createMappingTemplateVersion: vi.fn(),
  startJsonIngest: vi.fn(),
  checkMappingCompatibility: vi.fn(),
}))

const { profileJson } = await import('../../api/structured')
const profileJsonMock = vi.mocked(profileJson)

vi.mock('../../api/docs', () => ({
  uploadDoc: vi.fn().mockResolvedValue({ doc_id: 11, job_id: 1, status: 'queued' }),
  listDocs: vi.fn().mockResolvedValue([]),
  deleteDoc: vi.fn(),
  reindexDoc: vi.fn(),
  docStatus: vi.fn(),
  getDocRaw: vi.fn(),
  normalizeDocStatus: (s: string) => s,
}))

vi.mock('../../api/retrieval', () => ({
  listChunks: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '5' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

const { uploadDoc } = await import('../../api/docs')
const uploadDocMock = vi.mocked(uploadDoc)
import type { UploadDocResult } from '../../api/docs'

const globalMount = {
  global: { plugins: [ElementPlus] },
}

/**
 * UploadDialog 无 append-to-body：dialog 内容渲染在 wrapper.element
 * （teleport 注释节点）的父元素内；JsonMappingWizard 有 append-to-body：
 * 内容渲染在 document.body。两类内容都需要 flush 后才出现在 DOM 中。
 */
function dialogScope(wrapper: VueWrapper): Element {
  return wrapper.element.parentElement as Element
}

function queryFileInput(scope: Element): HTMLInputElement | null {
  return scope.querySelector<HTMLInputElement>('input[type="file"]')
}

function buttonsByText(scope: Element, text: string): HTMLButtonElement[] {
  return [...scope.querySelectorAll<HTMLButtonElement>('button')].filter((b) =>
    b.textContent?.includes(text),
  )
}

async function pickFileIn(scope: Element, file: File) {
  await pickFilesIn(scope, [file])
}

async function pickFilesIn(scope: Element, files: File[]) {
  const input = queryFileInput(scope)
  if (!input) throw new Error('no file input found in scope')
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}

/** 结构探查响应 fixture */
const profileFixture = {
  source_format: 'json' as const,
  doc_id: 11,
  candidates: [],
  fingerprint: 'fp',
  total_records_estimate: 0,
  sampled_records: 0,
  warnings: [],
}

async function mountDialog(
  component: typeof UploadDialog | typeof JsonMappingWizard,
  props: Record<string, unknown>,
): Promise<VueWrapper<unknown>> {
  const wrapper = mount(component, { props: props as never, ...globalMount })
  await flushPromises()
  return wrapper
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('UploadDialog file routing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('selecting a TXT file calls the existing uploadDoc and enqueues legacy upload', async () => {
    const wrapper = await mountDialog(UploadDialog, { visible: true, kbId: 5 })
    await pickFileIn(dialogScope(wrapper), new File(['hello'], 'note.txt', { type: 'text/plain' }))

    expect(uploadDocMock).toHaveBeenCalledWith(5, expect.any(File))
  })

  it('rejects JSON in the ordinary document flow', async () => {
    const wrapper = await mountDialog(UploadDialog, { visible: true, kbId: 5 })
    await pickFileIn(dialogScope(wrapper), new File(['{}'], 'posts.json', { type: 'application/json' }))

    expect(uploadDocMock).not.toHaveBeenCalled()
    expect(dialogScope(wrapper).textContent).toContain('不支持的普通文档类型')
  })

  it('selecting an unsupported extension shows an inline error and uploads nothing', async () => {
    const wrapper = await mountDialog(UploadDialog, { visible: true, kbId: 5 })
    await pickFileIn(dialogScope(wrapper), new File(['x'], 'image.png', { type: 'image/png' }))

    expect(uploadDocMock).not.toHaveBeenCalled()
    const err = dialogScope(wrapper).querySelector('.upload-drop__inline-error')
    expect(err).not.toBeNull()
    expect(err?.textContent).toContain('不支持的普通文档类型')
  })
})

describe('KbDocsView upload type routing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('opens the type selector before either upload flow', async () => {
    const wrapper = mount(KbDocsView, globalMount)
    await flushPromises()

    await wrapper.find('.kb-docs__actions button').trigger('click')

    expect(wrapper.findComponent(UploadTypeDialog).props('visible')).toBe(true)
    expect(wrapper.findComponent(UploadDialog).props('visible')).toBe(false)
    expect(wrapper.findComponent(JsonMappingWizard).exists()).toBe(false)
  })

  it('opens the ordinary upload dialog after choosing ordinary documents', async () => {
    const wrapper = mount(KbDocsView, globalMount)
    await flushPromises()

    wrapper.findComponent(UploadTypeDialog).vm.$emit('select', 'document')
    await flushPromises()

    expect(wrapper.findComponent(UploadDialog).props('visible')).toBe(true)
    expect(wrapper.findComponent(JsonMappingWizard).exists()).toBe(false)
  })

  it('opens JsonMappingWizard after choosing structured data', async () => {
    const wrapper = mount(KbDocsView, globalMount)
    await flushPromises()

    const typeDialog = wrapper.findComponent(UploadTypeDialog)
    typeDialog.vm.$emit('select', 'structured')
    await flushPromises()

    const wizard = wrapper.findComponent(JsonMappingWizard)
    expect(wizard.exists()).toBe(true)
    expect(wrapper.findComponent(UploadDialog).props('visible')).toBe(false)
    // 向导步骤头渲染在 body（dialog teleport）
    expect(document.body.querySelectorAll('.wizard-step')).toHaveLength(6)
  })
})

describe('JsonMappingWizard shell', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the six-step header and upload step initially', async () => {
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })

    expect(document.body.querySelectorAll('.wizard-step')).toHaveLength(6)
    expect(document.body.textContent).toContain('选择 JSON / JSONL 文件')
  })

  it('displays the 100 MB limit hint before selection', async () => {
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })

    expect(document.body.textContent).toContain('100 MB')
  })

  it('disables the primary action until a file is selected', async () => {
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })

    // 未选文件时主操作显示「下一步」且禁用
    const primary = buttonsByText(document.body, '下一步')
    expect(primary).toHaveLength(1)
    expect(primary[0].disabled).toBe(true)

    await pickFileIn(document.body, new File(['{}'], 'posts.json', { type: 'application/json' }))

    // 选择文件后主操作变为「开始探查」且可用
    const primary2 = buttonsByText(document.body, '开始探查')
    expect(primary2).toHaveLength(1)
    expect(primary2[0].disabled).toBe(false)
  })

  it('shows the backend error message when structured upload fails', async () => {
    uploadDocMock.mockRejectedValueOnce({
      error: { code: 'PARSER_NOT_FOUND', message: '不支持的文件类型: .json' },
    })
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })
    await pickFileIn(document.body, new File(['{}'], 'posts.json', { type: 'application/json' }))

    await buttonsByText(document.body, '开始探查')[0].click()
    await flushPromises()

    expect(document.body.textContent).toContain('不支持的文件类型: .json')
    expect(document.body.textContent).not.toContain('操作失败，请重试')
  })

  it('continue-configuration mode profiles the preset doc directly without re-upload', async () => {
    profileJsonMock.mockResolvedValue({
      source_format: 'json',
      doc_id: 42,
      candidates: [],
      fingerprint: 'fp',
      total_records_estimate: 0,
      sampled_records: 0,
      warnings: [],
    })
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5, docId: 42 })

    // 继续配置模式：无需选择文件，「开始探查」直接可用
    const primary = buttonsByText(document.body, '开始探查')
    expect(primary).toHaveLength(1)
    expect(primary[0].disabled).toBe(false)

    await primary[0].click()
    await flushPromises()

    // 直接对预置 doc 探查，不再走上传
    expect(profileJsonMock).toHaveBeenCalledWith(5, 42)
    // 进入结构检测步骤（展示候选路径元信息）
    expect(document.body.textContent).toContain('检测到 0 个候选记录路径')
  })
})

describe('JsonMappingWizard batch upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })
  // 仅在本 describe 内清理 ElMessageBox spy（restoreAllMocks 会连带清掉
  // 工厂 vi.fn 的默认实现，不能放在文件级 afterEach）
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uploads multiple selected files in order and profiles the first doc', async () => {
    uploadDocMock
      .mockResolvedValueOnce({ doc_id: 11, job_id: 1, status: 'queued' })
      .mockResolvedValueOnce({ doc_id: 12, job_id: 2, status: 'queued' })
    profileJsonMock.mockResolvedValue(profileFixture)
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })

    await pickFilesIn(document.body, [
      new File(['{}'], 'a.json', { type: 'application/json' }),
      new File(['{}'], 'b.json', { type: 'application/json' }),
    ])
    await buttonsByText(document.body, '开始探查')[0].click()
    await flushPromises()

    expect(uploadDocMock).toHaveBeenCalledTimes(2)
    expect(uploadDocMock.mock.calls[0][0]).toBe(5)
    expect((uploadDocMock.mock.calls[0][1] as File).name).toBe('a.json')
    expect((uploadDocMock.mock.calls[1][1] as File).name).toBe('b.json')
    // 探查使用第一个上传成功的 doc_id
    expect(profileJsonMock).toHaveBeenCalledWith(5, 11)
  })

  it('continues with successful uploads when one file fails, and retries the failed file after confirm', async () => {
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    uploadDocMock
      .mockResolvedValueOnce({ doc_id: 11, job_id: 1, status: 'queued' })
      .mockRejectedValueOnce({ error: { code: 'PARSE_FAILED', message: '解析失败' } })
    profileJsonMock.mockResolvedValue(profileFixture)
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })

    await pickFilesIn(document.body, [
      new File(['{}'], 'a.json', { type: 'application/json' }),
      new File(['{}'], 'b.json', { type: 'application/json' }),
    ])
    // 第一轮：b.json 失败，成功项继续探查
    await buttonsByText(document.body, '开始探查')[0].click()
    await flushPromises()
    expect(profileJsonMock).toHaveBeenCalledWith(5, 11)

    // 返回上传步骤，重试失败项：弹确认框，文案含失败文件名
    await buttonsByText(document.body, '返回')[0].click()
    await flushPromises()
    await buttonsByText(document.body, '开始探查')[0].click()
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0][0]).toContain('b.json')
    // 确认「跳过并继续」后重试成功，用成功的 doc_id 探查
    expect(uploadDocMock).toHaveBeenCalledTimes(3)
    expect(profileJsonMock).toHaveBeenLastCalledWith(5, 11)
  })

  it('shows an all-failed error without profiling when every upload fails', async () => {
    uploadDocMock.mockRejectedValue(new Error('网络错误'))
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })
    await pickFileIn(document.body, new File(['{}'], 'x.json', { type: 'application/json' }))

    await buttonsByText(document.body, '开始探查')[0].click()
    await flushPromises()

    expect(profileJsonMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('所有文件上传失败')
  })

  it('shows upload progress text while the batch is uploading', async () => {
    let resolveFirst!: (v: UploadDocResult) => void
    uploadDocMock
      .mockImplementationOnce(() => new Promise<UploadDocResult>((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ doc_id: 12, job_id: 2, status: 'queued' })
    profileJsonMock.mockResolvedValue(profileFixture)
    await mountDialog(JsonMappingWizard, { visible: true, kbId: 5 })

    await pickFilesIn(document.body, [
      new File(['{}'], 'a.json', { type: 'application/json' }),
      new File(['{}'], 'b.json', { type: 'application/json' }),
    ])
    const primary = buttonsByText(document.body, '开始探查')[0]
    await primary.click()
    await flushPromises()

    // 首个文件仍在上传：进度文案可见
    expect(document.body.textContent).toContain('上传中')
    expect(document.body.textContent).toContain('a.json')

    resolveFirst({ doc_id: 11, job_id: 1, status: 'queued' })
    await flushPromises()

    expect(uploadDocMock).toHaveBeenCalledTimes(2)
    expect(profileJsonMock).toHaveBeenCalledWith(5, 11)
  })
})
