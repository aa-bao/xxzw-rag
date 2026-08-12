import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BatchDocEntry } from './useJsonMappingWizard'

vi.mock('../../api/structured', () => ({
  listMappingTemplates: vi.fn().mockResolvedValue([]),
  createMappingTemplateVersion: vi.fn().mockResolvedValue({ mapping_version_id: 1, version: 1 }),
  checkMappingCompatibility: vi.fn(),
  startJsonIngest: vi.fn(
    async (_kbId: number, payload: { doc_id: number; mapping_version_id: number }) => ({
      doc_id: payload.doc_id,
      job_id: payload.doc_id * 10,
      mapping_version_id: payload.mapping_version_id,
      status: 'queued',
    }),
  ),
}))

vi.mock('./useJsonMappingWizard', async () => {
  const { computed, ref } = await import('vue')
  const { startJsonIngest } = await import('../../api/structured')
  const state = ref({
    step: 'confirm',
    docId: 11,
    mapping: {
      source_format: 'json',
      record_types: [
        {
          name: 'record', record_path: '$', chunk_policy: 'semantic', relation_rule: null,
          fields: [{ path: 'body', role: 'content', name: 'body', transforms: [], required: true }],
          children: [],
        },
      ],
    },
    rows: [],
  })
  const batchDocs = ref<BatchDocEntry[] | null>(null)
  /** 与 hook 契约一致的入库循环：跳过已入库，失败不中断，返回成功项 */
  const confirmIngest = vi.fn(async (mappingVersionId: number) => {
    const succeeded: { doc_id: number; job_id: number; mapping_version_id: number; status: string }[] = []
    for (const entry of batchDocs.value ?? []) {
      if (entry.status === 'ingested') continue
      try {
        const result = await startJsonIngest(5, { doc_id: entry.docId, mapping_version_id: mappingVersionId })
        entry.status = 'ingested'
        entry.jobId = result.job_id
        entry.error = null
        succeeded.push(result)
      } catch (err) {
        entry.status = 'failed'
        entry.error = err instanceof Error ? err.message : String(err)
      }
    }
    return succeeded
  })
  const singleton = {
    state,
    busy: ref(null),
    ingested: ref(null),
    stepNumber: computed(() => 6),
    setFiles: vi.fn(),
    setDocForResume: vi.fn(),
    setBatchDocs: vi.fn(),
    batchDocs,
    profile: vi.fn(),
    selectCandidate: vi.fn(),
    next: vi.fn(),
    back: vi.fn(),
    preview: vi.fn(),
    applyMappingUpdate: vi.fn(),
    setStateMapping: vi.fn(),
    confirmIngest,
  }
  return { useJsonMappingWizard: () => singleton }
})

/** 测试访问 mock 状态机单例的入口（同一批 ref 被所有 useJsonMappingWizard() 调用共享） */
const wizardMock = async () => (await import('./useJsonMappingWizard')).useJsonMappingWizard(5)

const { startJsonIngest } = await import('../../api/structured')
const startJsonIngestMock = vi.mocked(startJsonIngest)

function batchEntry(docId: number, name: string, status: BatchDocEntry['status'], jobId: number | null = null, error: string | null = null): BatchDocEntry {
  return { docId, name, fileSizeBytes: null, status, jobId, error }
}

/** 已挂载的 wrapper：测试间卸载并清理 body，避免残留对话框干扰后续用例 */
const mountedWrappers: VueWrapper<unknown>[] = []

async function mountWizard() {
  const wrapper = mount((await import('./JsonMappingWizard.vue')).default, {
    props: { visible: true, kbId: 5 },
    global: { plugins: [ElementPlus] },
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  document.body.innerHTML = ''
})

/** 勾选确认复选框并点击主操作按钮 */
async function acknowledgeAndSubmit(buttonText: string) {
  const input = document.body.querySelector<HTMLInputElement>('.json-confirm__final input')
  if (!input) throw new Error('confirm checkbox not found')
  input.checked = true
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()

  const button = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.includes(buttonText))
  if (!button) throw new Error(`button "${buttonText}" not found`)
  button.click()
  await flushPromises()
}

describe('JsonMappingWizard confirm action', () => {
  it('keeps the confirm button visible and disabled until acknowledgement', async () => {
    await mountWizard()

    const button = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.includes('确认入库'))
    expect(button).toBeDefined()
    expect(button?.disabled).toBe(true)
  })

  it('renders batch docs in the confirm step and retries failed entries while skipping ingested ones', async () => {
    (await wizardMock()).batchDocs.value = [
      batchEntry(11, 'a.json', 'ingested', 110),
      batchEntry(12, 'b.json', 'failed', null, '解析失败'),
      batchEntry(13, 'c.json', 'pending'),
    ]
    await mountWizard()

    // JsonConfirmStep 渲染入库文件状态
    expect(document.body.textContent).toContain('入库文件')
    expect(document.body.textContent).toContain('a.json')
    expect(document.body.textContent).toContain('已入库')
    expect(document.body.textContent).toContain('b.json')
    expect(document.body.textContent).toContain('失败')
    // 主按钮显示重试失败项数量
    expect(document.body.textContent).toContain('重试失败项(1)')

    await acknowledgeAndSubmit('重试失败项(1)')

    // 已入库项被跳过；失败项与待处理项重新提交
    expect(startJsonIngestMock).toHaveBeenCalledWith(5, { doc_id: 12, mapping_version_id: 1 })
    expect(startJsonIngestMock).toHaveBeenCalledWith(5, { doc_id: 13, mapping_version_id: 1 })
    expect(startJsonIngestMock).not.toHaveBeenCalledWith(5, { doc_id: 11, mapping_version_id: 1 })
  })

  it('emits one ingested event per successful doc with docId/jobId payloads', async () => {
    (await wizardMock()).batchDocs.value = [
      batchEntry(11, 'a.json', 'pending'),
      batchEntry(12, 'b.json', 'pending'),
    ]
    const wrapper = await mountWizard()

    await acknowledgeAndSubmit('确认入库')

    const ingested = wrapper.emitted('ingested')
    expect(ingested).toHaveLength(2)
    expect(ingested?.[0]?.[0]).toEqual({ docId: 11, jobId: 110, name: 'a.json', fileSizeBytes: null })
    expect(ingested?.[1]?.[0]).toEqual({ docId: 12, jobId: 120, name: 'b.json', fileSizeBytes: null })
  })

  it('emits close when the whole batch succeeds', async () => {
    (await wizardMock()).batchDocs.value = [batchEntry(11, 'a.json', 'pending')]
    const wrapper = await mountWizard()

    await acknowledgeAndSubmit('确认入库')

    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(wrapper.emitted('ingested')).toHaveLength(1)
  })
})
