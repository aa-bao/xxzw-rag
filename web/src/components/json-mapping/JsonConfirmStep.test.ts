import { describe, it, expect, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElCheckbox, ElRadioGroup, ElSelect } from 'element-plus'
import JsonConfirmStep from './JsonConfirmStep.vue'
import type { Compatibility, MappingDefinition, MappingTemplateSummary } from '../../types/structured'

const mapping: MappingDefinition = {
  source_format: 'json',
  record_types: [
    {
      name: 'post',
      record_path: '$',
      chunk_policy: 'semantic',
      relation_rule: null,
      fields: [
        { path: '$.id', role: 'id', name: 'id', transforms: [], required: true },
        { path: '$.body', role: 'content', name: 'body', transforms: [], required: false },
        { path: '$.tags', role: 'keyword', name: 'tags', transforms: [], required: false },
      ],
      children: [
        {
          name: 'comment',
          record_path: 'comments[*]',
          chunk_policy: 'parent-only',
          relation_rule: null,
          fields: [{ path: 'comments[*].text', role: 'content', name: 'text', transforms: [], required: false }],
          children: [],
        },
      ],
    },
  ],
}

const template: MappingTemplateSummary = {
  id: 7,
  name: '博客文章',
  source_format: 'json',
  current_version: 2,
  fingerprint: 'fp-existing',
  usage_count: 3,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: null,
  versions: [
    { id: 101, version: 1, mapping, fingerprint: 'fp-v1', usage_count: 1, created_at: null, created_by: null },
    { id: 102, version: 2, mapping, fingerprint: 'fp-existing', usage_count: 2, created_at: null, created_by: null },
  ],
}

type StepWrapper = VueWrapper<unknown>

const mountStep = (props?: Record<string, unknown>): StepWrapper =>
  mount(JsonConfirmStep, {
    props: {
      mapping,
      templates: [template],
      totalRows: 12,
      warningCount: 2,
      compatibility: null,
      ...props,
    },
    global: { plugins: [ElementPlus] },
  })

function vmOf(wrapper: StepWrapper) {
  return wrapper.vm as unknown as {
    checked: boolean
    breakingConfirmed: boolean
    submitPayload: () => { template_id: number | null; name: string | undefined }
    versionToUse: { version: number } | null
    needsReconfirm: boolean
  }
}

afterEach(() => {
  document.body.innerHTML = ''
})

/** 切换到「更新现有模板」模式（受控 radio group：直接 emit 新值） */
async function switchToExisting(wrapper: StepWrapper) {
  const groups = wrapper.findAllComponents(ElRadioGroup)
  groups[0].vm.$emit('update:modelValue', 'existing')
  await flushPromises()
}

describe('JsonConfirmStep', () => {
  it('requires the confirm checkbox before ingestion is allowed', async () => {
    const wrapper = mountStep()
    await flushPromises()

    expect(vmOf(wrapper).checked).toBe(false)
    await wrapper.findAllComponents(ElCheckbox)[0].vm.$emit('update:model-value', true)
    expect(vmOf(wrapper).checked).toBe(true)
  })

  it('submits a new template with the given name', async () => {
    const wrapper = mountStep()
    await flushPromises()

    const input = wrapper.find('input[placeholder="模板名称（如：博客文章评论）"]')
    await input.setValue('我的新模板')
    expect(vmOf(wrapper).submitPayload()).toEqual({ template_id: null, name: '我的新模板' })
  })

  it('reuses the existing version when fingerprints match', async () => {
    const wrapper = mountStep({
      compatibility: { kind: 'fingerprint_match', added_optional_fields: [], removed_paths: [], changed_paths: [], detail: null } satisfies Compatibility,
    })
    await flushPromises()

    // 切到「更新现有模板」
    await switchToExisting(wrapper)
    // 选择模板
    await wrapper.findAllComponents(ElSelect)[0].vm.$emit('update:model-value', 7)
    await flushPromises()

    expect(vmOf(wrapper).versionToUse?.version).toBe(2)
    expect(wrapper.text()).toContain('直接复用该版本')
    expect(vmOf(wrapper).needsReconfirm).toBe(false)
  })

  it('creates a new version for compatible changes and shows the optional-field notice', async () => {
    const wrapper = mountStep({
      compatibility: {
        kind: 'compatible',
        added_optional_fields: ['$.summary'],
        removed_paths: [],
        changed_paths: [],
        detail: null,
      } satisfies Compatibility,
    })
    await flushPromises()

    await switchToExisting(wrapper)
    await wrapper.findAllComponents(ElSelect)[0].vm.$emit('update:model-value', 7)
    await flushPromises()

    expect(vmOf(wrapper).versionToUse).toBeNull()
    expect(wrapper.text()).toContain('兼容')
    expect(wrapper.text()).toContain('新增可选字段')
    expect(vmOf(wrapper).needsReconfirm).toBe(true)
  })

  it('requires breaking-change reconfirmation before ingest', async () => {
    const wrapper = mountStep({
      compatibility: {
        kind: 'breaking',
        added_optional_fields: [],
        removed_paths: ['$.body'],
        changed_paths: ['$.comments'],
        detail: null,
      } satisfies Compatibility,
    })
    await flushPromises()

    await switchToExisting(wrapper)
    await wrapper.findAllComponents(ElSelect)[0].vm.$emit('update:model-value', 7)
    await flushPromises()

    expect(wrapper.text()).toContain('破坏性变更')
    expect(vmOf(wrapper).needsReconfirm).toBe(true)
    expect(vmOf(wrapper).breakingConfirmed).toBe(false)
    // 勾选破坏性确认后放行
    await wrapper.findAllComponents(ElCheckbox)[0].vm.$emit('update:model-value', true)
    expect(vmOf(wrapper).breakingConfirmed).toBe(true)
  })

  it('shows an immutable used-version summary in the ingest summary', async () => {
    const wrapper = mountStep({
      compatibility: { kind: 'fingerprint_match', added_optional_fields: [], removed_paths: [], changed_paths: [], detail: null } satisfies Compatibility,
    })
    await flushPromises()

    await switchToExisting(wrapper)
    await wrapper.findAllComponents(ElSelect)[0].vm.$emit('update:model-value', 7)
    await flushPromises()

    const text = wrapper.text()
    // 摘要展示记录类型、策略与版本
    expect(text).toContain('post')
    expect(text).toContain('comment')
    expect(text).toContain('语义切块')
    expect(text).toContain('并入父记录')
    expect(text).toContain('v2')
    // 使用中的版本不可修改（无编辑入口，只读展示）
    expect(wrapper.findAll('input[placeholder*="模板名称"]')).toHaveLength(0)
  })
})
