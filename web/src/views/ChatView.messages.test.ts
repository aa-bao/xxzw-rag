import { MotionPlugin } from '@vueuse/motion'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ChatView from './ChatView.vue'

const { queryRaw, streamSse } = vi.hoisted(() => ({
  queryRaw: vi.fn(),
  streamSse: vi.fn(),
}))

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
  renameConversation: vi.fn(),
  getMessages: vi.fn().mockResolvedValue([
    {
      role: 'user',
      content: 'first question',
      status: 'completed',
      created_at: null,
      references: [],
    },
    {
      role: 'assistant',
      content: 'first answer',
      status: 'completed',
      created_at: null,
      references: [],
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
  queryRaw,
}))

vi.mock('../api/sse', () => ({ streamSse }))

const ElInputStub = defineComponent({
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  template:
    '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
})

describe('ChatView message visibility', () => {
  beforeEach(() => {
    queryRaw.mockReset().mockResolvedValue({ ok: true })
    streamSse.mockReset().mockImplementation(async function* () {
      yield { event: 'chunk', data: JSON.stringify({ content: 'second answer' }) }
      yield { event: 'done', data: '{}' }
    })
  })

  it('keeps the second user message visible after the streamed answer completes', async () => {
    const wrapper = mount(ChatView, {
      attachTo: document.body,
      global: {
        plugins: [MotionPlugin],
        stubs: {
          'el-button': { template: '<button><slot /></button>' },
          'el-icon': { template: '<span><slot /></span>' },
          'el-input': ElInputStub,
          'el-option': true,
          'el-select': { template: '<select><slot /></select>' },
        },
      },
    })
    await flushPromises()

    await wrapper.get('textarea').setValue('second question')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 1000))
    await flushPromises()

    const userMessages = wrapper.findAll('.chat-view__msg--user')
    expect(userMessages).toHaveLength(2)
    expect(userMessages[1].text()).toContain('second question')
    expect(getComputedStyle(userMessages[1].element).opacity).not.toBe('0')

    wrapper.unmount()
  })
})
