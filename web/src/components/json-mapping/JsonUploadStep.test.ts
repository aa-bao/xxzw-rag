import { describe, it, expect, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import JsonUploadStep from './JsonUploadStep.vue'

type StepWrapper = VueWrapper<unknown>

function mountStep(props?: Record<string, unknown>): StepWrapper {
  return mount(JsonUploadStep, {
    props: {
      files: [],
      busy: null,
      error: '',
      ...props,
    },
    global: {
      stubs: {
        'el-progress': { template: '<div />' },
        'el-icon': { template: '<span><slot /></span>' },
      },
    },
  })
}

/** 改写 input.files 后派发 change 事件 */
async function pickFilesIn(wrapper: StepWrapper, files: File[]) {
  const input = wrapper.get('input[type=file]')
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  return input.trigger('change')
}

function pickPayload(wrapper: StepWrapper): File[] {
  return wrapper.emitted('pick')?.[0]?.[0] as File[]
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('JsonUploadStep', () => {
  it('emits all files when multiple files are selected at once', async () => {
    const wrapper = mountStep()
    await pickFilesIn(wrapper, [
      new File(['{"a":1}'], 'a.json'),
      new File(['{"b":1}'], 'b.json'),
      new File(['{"c":1}'], 'c.json'),
    ])

    const payload = pickPayload(wrapper)
    expect(payload).toHaveLength(3)
    expect(payload.map((f) => f.name)).toEqual(['a.json', 'b.json', 'c.json'])
  })

  it('truncates to 20 files and shows a notice when more than 20 are picked', async () => {
    const wrapper = mountStep()
    const files = Array.from({ length: 25 }, (_, i) => new File([`{"i":${i}}`], `f${i}.json`))
    await pickFilesIn(wrapper, files)

    expect(pickPayload(wrapper)).toHaveLength(20)
    expect(wrapper.text()).toContain('最多')
    expect(wrapper.text()).toContain('已保留前 20 个')
  })

  it('rejects a file larger than 100 MB and shows its name', async () => {
    const wrapper = mountStep()
    const big = new File([new ArrayBuffer(100 * 1024 * 1024 + 1)], 'big.json')
    await pickFilesIn(wrapper, [big])

    expect(wrapper.emitted('pick')).toBeUndefined()
    expect(wrapper.text()).toContain('big.json')
    expect(wrapper.text()).toContain('100 MB')
  })

  it('emits clear with the index when a remove button is clicked', async () => {
    const wrapper = mountStep({
      files: [new File(['a'], 'a.json'), new File(['b'], 'b.json')],
    })

    const buttons = wrapper.findAll('button.json-upload__clear')
    expect(buttons).toHaveLength(2)
    await buttons[1].trigger('click')

    expect(wrapper.emitted('clear')?.[0]).toEqual([1])
    // 移除按钮 aria-label 包含文件名
    expect(wrapper.find('button[aria-label*="a.json"]').exists()).toBe(true)
  })

  it('disables remove buttons while busy', async () => {
    const wrapper = mountStep({
      files: [new File(['a'], 'a.json'), new File(['b'], 'b.json')],
      busy: 'upload',
    })

    const buttons = wrapper.findAll('button.json-upload__clear')
    expect(buttons).toHaveLength(2)
    for (const btn of buttons) {
      expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it('emits all dropped files', async () => {
    const wrapper = mountStep()
    await wrapper.find('.json-upload__drop').trigger('drop', {
      dataTransfer: { files: [new File(['{"a":1}'], 'a.json'), new File(['{"b":1}'], 'b.json')] },
    })

    const payload = pickPayload(wrapper)
    expect(payload).toHaveLength(2)
    expect(payload.map((f) => f.name)).toEqual(['a.json', 'b.json'])
  })
})
