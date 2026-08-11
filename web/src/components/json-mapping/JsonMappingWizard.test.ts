import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import KbDocsView from '../../views/KbDocsView.vue'
import JsonMappingWizard from './JsonMappingWizard.vue'
import UploadDialog from '../UploadDialog.vue'

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
  const input = queryFileInput(scope)
  if (!input) throw new Error('no file input found in scope')
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
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

  it('selecting a JSON file emits open-json-wizard and does not enqueue legacy upload', async () => {
    const wrapper = await mountDialog(UploadDialog, { visible: true, kbId: 5 })
    await pickFileIn(dialogScope(wrapper), new File(['{}'], 'posts.json', { type: 'application/json' }))

    expect(wrapper.emitted('open-json-wizard')).toHaveLength(1)
    expect((wrapper.emitted('open-json-wizard')![0] as [File])[0].name).toBe('posts.json')
    expect(uploadDocMock).not.toHaveBeenCalled()
  })

  it('selecting a JSONL file emits open-json-wizard without enqueueing legacy upload', async () => {
    const wrapper = await mountDialog(UploadDialog, { visible: true, kbId: 5 })
    await pickFileIn(
      dialogScope(wrapper),
      new File(['{"a":1}\n'], 'records.jsonl', { type: 'application/jsonl' }),
    )

    expect(wrapper.emitted('open-json-wizard')).toHaveLength(1)
    expect(uploadDocMock).not.toHaveBeenCalled()
  })

  it('selecting an unsupported extension shows an inline error and uploads nothing', async () => {
    const wrapper = await mountDialog(UploadDialog, { visible: true, kbId: 5 })
    await pickFileIn(dialogScope(wrapper), new File(['x'], 'image.png', { type: 'image/png' }))

    expect(uploadDocMock).not.toHaveBeenCalled()
    const err = dialogScope(wrapper).querySelector('.upload-drop__inline-error')
    expect(err).not.toBeNull()
    expect(err?.textContent).toContain('不支持的文件类型')
  })
})

describe('KbDocsView JSON routing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('opens JsonMappingWizard when the upload dialog reports a JSON file', async () => {
    const wrapper = mount(KbDocsView, globalMount)
    await flushPromises()

    const dialog = wrapper.findComponent(UploadDialog)
    dialog.vm.$emit('open-json-wizard', new File(['{}'], 'posts.json', { type: 'application/json' }))
    await flushPromises()

    const wizard = wrapper.findComponent(JsonMappingWizard)
    expect(wizard.exists()).toBe(true)
    expect(dialog.props('visible')).toBe(false)
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
