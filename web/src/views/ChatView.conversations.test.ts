import { MotionPlugin } from '@vueuse/motion'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ChatView from './ChatView.vue'

const { createConversation, getMessages, listConversations, queryRaw, streamSse } =
  vi.hoisted(() => ({
    createConversation: vi.fn(),
    getMessages: vi.fn(),
    listConversations: vi.fn(),
    queryRaw: vi.fn(),
    streamSse: vi.fn(),
  }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
}))

vi.mock('../api/kb', () => ({
  listKbs: vi.fn().mockResolvedValue([
    { id: 1, name: '售后规则', cover_url: null },
    { id: 2, name: '平台规则', cover_url: null },
    { id: 3, name: '财务制度', cover_url: null },
  ]),
}))

vi.mock('../api/chat', () => ({
  createConversation,
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  getMessages,
  listConversations,
  queryRaw,
}))

vi.mock('../api/sse', () => ({ streamSse }))

const ElInputStub = defineComponent({
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  template:
    '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
})

function makeConv(
  id: string,
  kbIds: number[],
  updatedAt: string,
  title: string | null = null,
) {
  const kbNames = kbIds.map((n) => ({ 1: '售后规则', 2: '平台规则', 3: '财务制度' })[n])
  return {
    id,
    kb_ids: kbIds,
    kb_names: kbNames,
    kb_id: kbIds[0] ?? null,
    kb_name: kbIds[0] != null ? kbNames[0] : undefined,
    title,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: updatedAt,
  }
}

const CONVERSATIONS = [
  makeConv('conv-a', [1], '2026-08-10T10:00:00Z', '售后提问'),
  makeConv('conv-b', [1, 2], '2026-08-11T08:00:00Z', '双库问题'),
  makeConv('conv-c', [2, 1, 3], '2026-08-09T12:00:00Z', '三库问题'),
]

/** 渲染后的会话列表按 updated_at 倒序：conv-b(08-11) → conv-a(08-10) → conv-c(08-09) */
const RENDER_ORDER = ['双库问题', '售后提问', '三库问题']

function mountChatView() {
  return mount(ChatView, {
    attachTo: document.body,
    global: {
      plugins: [MotionPlugin],
      stubs: {
        'el-button': { template: '<button><slot /></button>' },
        'el-icon': { template: '<span><slot /></span>' },
        'el-input': ElInputStub,
      },
    },
  })
}

function newConversationBtn(wrapper: ReturnType<typeof mountChatView>) {
  return wrapper.findAll('button').find((b) => b.text().includes('新对话'))!
}

function pickKb(wrapper: ReturnType<typeof mountChatView>, name: string) {
  return wrapper
    .findAll('.chat-view__kb-pick')
    .find((b) => b.text().includes(name))!
    .trigger('click')
}

describe('ChatView conversation logic', () => {
  beforeEach(() => {
    createConversation.mockReset().mockResolvedValue({
      id: 'conv-new',
      kb_ids: [3],
      kb_id: 3,
    })
    getMessages.mockReset().mockResolvedValue([])
    listConversations.mockReset().mockResolvedValue([...CONVERSATIONS])
    queryRaw.mockReset().mockResolvedValue({ ok: true })
    streamSse.mockReset().mockImplementation(async function* () {
      yield { event: 'done', data: '{}' }
    })
  })

  it('shows all conversations by default, ordered by updated_at desc', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    const convs = wrapper.findAll('.chat-view__conv')
    expect(convs).toHaveLength(3)
    expect(convs.map((c) => c.find('.chat-view__conv-title').text())).toEqual(RENDER_ORDER)

    wrapper.unmount()
  })

  it('shows kb label with first two names and total count for multi-kb conversations', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    const convC = wrapper.findAll('.chat-view__conv')[2]
    expect(convC.find('.chat-view__conv-kb').text()).toContain('售后规则')
    expect(convC.find('.chat-view__conv-kb').text()).toContain('平台规则')
    expect(convC.find('.chat-view__conv-kb').text()).toContain('等3个')

    wrapper.unmount()
  })

  it('shows all history conversations after starting a new conversation and picking kbs', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    await newConversationBtn(wrapper).trigger('click')
    await flushPromises()
    await pickKb(wrapper, '财务制度')
    await flushPromises()

    const convs = wrapper.findAll('.chat-view__conv')
    expect(convs).toHaveLength(3)
    expect(convs.map((c) => c.find('.chat-view__conv-title').text())).toEqual(RENDER_ORDER)

    wrapper.unmount()
  })

  it('blocks sending the first message until at least one kb is selected', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    await newConversationBtn(wrapper).trigger('click')
    await flushPromises()

    await wrapper.get('textarea').setValue('hello')
    const sendBtn = wrapper.get('.chat-view__send-btn')
    expect(sendBtn.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('请选择本次对话使用的知识库')

    await pickKb(wrapper, '财务制度')
    await flushPromises()
    expect(sendBtn.attributes('disabled')).toBeUndefined()

    expect(createConversation).not.toHaveBeenCalled()
    expect(queryRaw).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('creates a conversation with the picked kb_ids on first send and inserts it at list top', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    await newConversationBtn(wrapper).trigger('click')
    await flushPromises()
    await pickKb(wrapper, '财务制度')
    await flushPromises()
    await wrapper.get('textarea').setValue('hello')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 1000))
    await flushPromises()

    expect(createConversation).toHaveBeenCalledTimes(1)
    expect(createConversation).toHaveBeenCalledWith([3])
    expect(queryRaw).toHaveBeenCalledTimes(1)
    expect(queryRaw).toHaveBeenCalledWith('conv-new', 'hello')

    const convs = wrapper.findAll('.chat-view__conv')
    expect(convs).toHaveLength(4)
    expect(convs[0].find('.chat-view__conv-title').text()).toBe('未命名对话')

    wrapper.unmount()
  })

  it('restores the bound kbs of an opened conversation as read-only', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    // 渲染顺序 [0] = conv-b（双库，08-11）
    await wrapper.findAll('.chat-view__conv')[0].find('.chat-view__conv-btn').trigger('click')
    await flushPromises()

    expect(getMessages).toHaveBeenCalledWith('conv-b')
    const display = wrapper.get('.chat-view__active-kbs')
    expect(display.text()).toContain('售后规则')
    expect(display.text()).toContain('平台规则')
    expect(display.attributes('readonly')).toBeDefined()
    expect(wrapper.find('.chat-view__kb-pick').exists()).toBe(false)

    wrapper.unmount()
  })

  it('keeps the full conversation list when switching between conversations', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    await wrapper.findAll('.chat-view__conv')[0].find('.chat-view__conv-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.chat-view__conv')[2].find('.chat-view__conv-btn').trigger('click')
    await flushPromises()

    const convs = wrapper.findAll('.chat-view__conv')
    expect(convs).toHaveLength(3)
    expect(convs.map((c) => c.find('.chat-view__conv-title').text())).toEqual(RENDER_ORDER)

    wrapper.unmount()
  })

  it('restores the bound kbs after switching away and back', async () => {
    const wrapper = mountChatView()
    await flushPromises()

    await wrapper.findAll('.chat-view__conv')[0].find('.chat-view__conv-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.chat-view__conv')[1].find('.chat-view__conv-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.chat-view__conv')[0].find('.chat-view__conv-btn').trigger('click')
    await flushPromises()

    const display = wrapper.get('.chat-view__active-kbs')
    expect(display.text()).toContain('售后规则')
    expect(display.text()).toContain('平台规则')

    wrapper.unmount()
  })
})
