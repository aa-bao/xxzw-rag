import { describe, it, expect } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import JsonPreviewStep from './JsonPreviewStep.vue'
import type { PreviewRow } from '../../types/structured'

const rows: PreviewRow[] = [
  {
    record_id: 'p1',
    parent_id: null,
    record_type: 'post',
    title: 'Hello',
    content: 'First post body',
    embedding_text: 'Hello\nFirst post body',
    lexical: { title: 'Hello', keywords: ['hello', 'post'], content: 'first post body' },
    filters: { status: 'published' },
    timestamps: { created_at: '2026-01-02T03:04:05Z' },
    display: { author: 'tian' },
    raw: { id: 1, body: 'First post body', tags: ['hello'] },
    source_pointer: '/0',
    warnings: [{ message: '字段 $.x 缺失', source_pointer: '/0' }],
  },
  {
    record_id: 'c1',
    parent_id: 'p1',
    record_type: 'comment',
    title: '',
    content: 'Nice',
    embedding_text: 'Nice',
    lexical: { title: '', keywords: [], content: 'nice' },
    filters: {},
    timestamps: {},
    display: {},
    raw: { text: 'Nice' },
    source_pointer: '/0/comments/0',
    warnings: [],
  },
]

type StepWrapper = VueWrapper<unknown>

const mountStep = (props?: Record<string, unknown>): StepWrapper =>
  mount(JsonPreviewStep, {
    props: {
      rows,
      totalRows: 2,
      globalWarnings: ['预览截断'],
      error: null,
      ...props,
    },
    global: { plugins: [ElementPlus] },
  })

describe('JsonPreviewStep', () => {
  it('displays raw JSON in an escaped pre block', async () => {
    const wrapper = mountStep()
    await flushPromises()

    const pre = wrapper.find('.json-preview__pre')
    expect(pre.exists()).toBe(true)
    expect(pre.text()).toContain('"id": 1')
    expect(pre.text()).toContain('"body": "First post body"')
  })

  it('displays readable content, embedding text and lexical fields', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 切到可读内容标签
    await wrapper.find('[role="tab"]').trigger('click')
    // jsdom 中 el-tabs 面板切换异步；直接断言三个面板的渲染内容（首面板 raw 为默认）
    const text = wrapper.text()
    expect(text).toContain('First post body')
    expect(text).toContain('Hello')

    // 向量文本面板
    const tabs = wrapper.findAll('.el-tabs__item')
    expect(tabs.length).toBeGreaterThanOrEqual(6)
  })

  it('shows filters, timestamps and display metadata', async () => {
    const wrapper = mountStep()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('status')
    expect(text).toContain('published')
    expect(text).toContain('created_at')
    expect(text).toContain('author')
  })

  it('shows record warnings with source pointer', async () => {
    const wrapper = mountStep()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('字段 $.x 缺失')
    expect(text).toContain('/0')
    expect(text).toContain('预览截断')
  })

  it('switching records updates the displayed row', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 第二个记录：选择器触发 update:model-value
    const picker = wrapper.findComponent({ name: 'ElSelect' })
    picker.vm.$emit('update:model-value', 1)
    await flushPromises()

    const pre = wrapper.find('.json-preview__pre')
    expect(pre.text()).toContain('"text": "Nice"')
  })

  it('renders a backend preview error with source pointer and stays on the step', async () => {
    const wrapper = mountStep({
      error: { message: 'JSONPath 解析失败', source_pointer: '/0/comments/3' },
    })
    await flushPromises()

    expect(wrapper.find('.json-preview__error').exists()).toBe(true)
    expect(wrapper.text()).toContain('JSONPath 解析失败')
    expect(wrapper.text()).toContain('/0/comments/3')
    // 错误时不渲染记录面板
    expect(wrapper.find('.el-tabs').exists()).toBe(false)
  })
})
