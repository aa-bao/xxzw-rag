import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import KbChunksView from './KbChunksView.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '5', docId: '569' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('../api/kb', () => ({
  getKb: vi.fn().mockResolvedValue({ id: 5, name: 'Test KB' }),
}))

vi.mock('../api/retrieval', () => ({
  listChunks: vi.fn(),
  updateChunk: vi.fn(),
}))

const { listChunks, updateChunk } = await import('../api/retrieval')
const listChunksMock = vi.mocked(listChunks)
const updateChunkMock = vi.mocked(updateChunk)

beforeEach(() => {
  vi.clearAllMocks()
  listChunksMock.mockResolvedValue({
    total: 1,
    items: [
      {
        chunk_id: '569:post-1:0:abc#0',
        content: 'A sentence cut in the wrong place',
        doc_id: 569,
        page: null,
        score: null,
        created_at: null,
      },
    ],
  })
})

describe('KbChunksView chunk editing', () => {
  it('edits a chunk inline and replaces the displayed content after saving', async () => {
    updateChunkMock.mockResolvedValue({
      chunk_id: '569:post-1:0:abc#0',
      content: 'A complete repaired sentence.',
      doc_id: 569,
      page: null,
      score: null,
      created_at: null,
    })
    const wrapper = mount(KbChunksView, {
      global: {
        plugins: [ElementPlus],
        directives: { motion: {} },
      },
    })
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('编辑'))!.trigger('click')
    const textarea = wrapper.find('textarea')
    await textarea.setValue('A complete repaired sentence.')
    await wrapper.findAll('button').find((button) => button.text().includes('保存修改'))!.trigger('click')
    await flushPromises()

    expect(updateChunkMock).toHaveBeenCalledWith(
      5,
      569,
      '569:post-1:0:abc#0',
      'A complete repaired sentence.',
    )
    expect(wrapper.text()).toContain('A complete repaired sentence.')
    expect(wrapper.find('textarea').exists()).toBe(false)
  })
})
