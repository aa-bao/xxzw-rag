import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ChatView from './ChatView.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { kb: '1' } }),
}))

vi.mock('../api/kb', () => ({
  listKbs: vi.fn().mockResolvedValue([
    { id: 1, name: 'Knowledge Base', cover_url: null },
  ]),
}))

vi.mock('../api/chat', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getMessages: vi.fn().mockResolvedValue([
    {
      role: 'assistant',
      content: 'Answer [2]',
      status: 'completed',
      created_at: null,
      references: [
        {
          chunk_id: 'first',
          doc_id: 1,
          title: 'Source One',
          snippet: 'first snippet',
          score: 0.9,
          page: 1,
        },
        {
          chunk_id: 'second',
          doc_id: 2,
          title: 'Source Two',
          snippet: 'second snippet',
          score: 0.8,
          page: 2,
        },
      ],
    },
  ]),
  listConversations: vi.fn().mockResolvedValue([
    {
      id: 'conversation-1',
      kb_ids: [1],
      kb_names: ['Knowledge Base'],
      kb_id: 1,
      title: 'Conversation',
      created_at: null,
      updated_at: '2026-08-10T00:00:00Z',
    },
  ]),
  queryRaw: vi.fn(),
}))

vi.mock('../api/sse', () => ({
  streamSse: vi.fn(),
}))

describe('ChatView citations', () => {
  it('opens the drawer at the clicked completed-message reference', async () => {
    const wrapper = mount(ChatView, {
      global: {
        directives: { motion: () => undefined },
        stubs: {
          'el-button': { template: '<button><slot /></button>' },
          'el-icon': { template: '<span><slot /></span>' },
          'el-input': { template: '<textarea />' },
          'el-option': true,
          'el-select': { template: '<select><slot /></select>' },
        },
      },
    })
    await flushPromises()

    const citation = wrapper.get(
      '.chat-view__inline-cite[data-reference-index="1"]',
    )
    await citation.trigger('click')

    const drawerRefs = wrapper.findAll('.chat-view__drawer-ref')
    expect(drawerRefs).toHaveLength(1)
    expect(drawerRefs[0].text()).toContain('[2]')
    expect(drawerRefs[0].text()).toContain('Source Two')
    expect(wrapper.text()).not.toContain('Source Onefirst snippet')
  })
})
