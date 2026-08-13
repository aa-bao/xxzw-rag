import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import UploadTypeDialog from './UploadTypeDialog.vue'

afterEach(() => {
  document.body.innerHTML = ''
})

describe('UploadTypeDialog', () => {
  it('offers ordinary and structured upload flows', async () => {
    mount(UploadTypeDialog, {
      props: { visible: true },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    expect(document.body.textContent).toContain('普通文档')
    expect(document.body.textContent).toContain('TXT、Markdown、DOCX')
    expect(document.body.textContent).toContain('结构化数据')
    expect(document.body.textContent).toContain('JSON、JSONL')
  })

  it.each([
    ['普通文档', 'document'],
    ['结构化数据', 'structured'],
  ] as const)('emits %s selection and closes', async (label, value) => {
    const wrapper = mount(UploadTypeDialog, {
      props: { visible: true },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const button = [...document.body.querySelectorAll<HTMLButtonElement>('.upload-type-card')]
      .find((item) => item.textContent?.includes(label))
    expect(button).toBeDefined()
    button?.click()
    await flushPromises()

    expect(wrapper.emitted('select')).toEqual([[value]])
    expect(wrapper.emitted('update:visible')).toContainEqual([false])
  })
})
