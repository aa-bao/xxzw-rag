import { describe, it, expect, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElSelect } from 'element-plus'
import JsonFieldMappingStep from './JsonFieldMappingStep.vue'
import { allowedTransformsFor } from './transformCatalog'
import type { MappingDefinition, SourceProfile } from '../../types/structured'

const profile: SourceProfile = {
  source_format: 'json',
  doc_id: 42,
  candidates: [
    {
      record_path: '$',
      record_count_estimate: 10,
      field_count: 3,
      nested_array_count: 1,
      confidence: 0.95,
      samples: ['{"id":1,"body":"hi"}'],
      fields: [
        { path: '$.id', inferred_type: 'number', sample: '1' },
        { path: '$.body', inferred_type: 'string', sample: 'hi' },
        { path: '$.comments', inferred_type: 'array', sample: '[...]' },
      ],
      suggested_mapping: {
        source_format: 'json',
        record_types: [],
      },
    },
  ],
  fingerprint: 'fp',
  total_records_estimate: 10,
  sampled_records: 5,
  warnings: [],
}

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
        { path: '$.body', role: 'title', name: 'body', transforms: [], required: false },
      ],
      children: [],
    },
  ],
}

type StepWrapper = VueWrapper<unknown>

const mountStep = (m = mapping): StepWrapper =>
  mount(JsonFieldMappingStep, {
    props: { profile, mapping: m },
    global: { plugins: [ElementPlus] },
  })

/** 元素顺序即文档顺序：每行 1 个角色下拉 + 1 个转换添加下拉 */
function selects(wrapper: StepWrapper) {
  return wrapper.findAllComponents(ElSelect)
}

/** 第 i 行（0 基）的转换添加下拉 */
function addSelect(wrapper: StepWrapper, row: number) {
  return selects(wrapper)[row * 2 + 1]
}

/** 某字段的转换添加下拉选项文本（面板渲染在 body，以其 aria-label 为键） */
function addDropdownOptions(path: string): string {
  const list = [...document.body.querySelectorAll<HTMLElement>('ul.el-select-dropdown__list')].find(
    (l) => l.getAttribute('aria-label') === `给 ${path} 添加转换`,
  )
  return list?.textContent ?? ''
}

function lastMapping(wrapper: StepWrapper): MappingDefinition {
  const emitted = wrapper.emitted('update:mapping')!
  return emitted[emitted.length - 1][0] as MappingDefinition
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('JsonFieldMappingStep', () => {
  it('renders one row per field with sample, inferred type and role select', async () => {
    const wrapper = mountStep()
    await flushPromises()

    expect(wrapper.findAll('.json-fields__row')).toHaveLength(2)
    const texts = wrapper.text()
    expect(texts).toContain('$.id')
    expect(texts).toContain('$.body')
    expect(texts).toContain('数字')
    expect(texts).toContain('字符串')
    expect(texts).toContain('记录ID')
    expect(texts).toContain('标题')
  })

  it('marks keyword with 仅关键词检索 and filter with 仅筛选 labels', async () => {
    const wrapper = mountStep({
      ...mapping,
      record_types: [
        {
          ...mapping.record_types[0],
          fields: [
            { path: '$.id', role: 'id', name: 'id', transforms: [], required: true },
            { path: '$.tags', role: 'keyword', name: 'tags', transforms: [], required: false },
            { path: '$.status', role: 'filter', name: 'status', transforms: [], required: false },
          ],
        },
      ],
    })
    await flushPromises()

    expect(wrapper.text()).toContain('仅关键词检索')
    expect(wrapper.text()).toContain('仅筛选')
  })

  it('role change updates only that field via update:mapping', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 第二行 $.body 的角色下拉：改为 content
    selects(wrapper)[2].vm.$emit('update:model-value', 'content')

    const updated = lastMapping(wrapper)
    const fields = updated.record_types[0].fields
    expect(fields[0]).toEqual(mapping.record_types[0].fields[0]) // 未被触碰的字段原样保留
    expect(fields[1].role).toBe('content')
  })

  it('adds and removes transforms', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // $.body 是 string：可添加 strip_html
    addSelect(wrapper, 1).vm.$emit('update:model-value', 'strip_html')
    // 受控组件：把新映射回喂 props 模拟父组件（向导）应用更新
    await wrapper.setProps({ mapping: lastMapping(wrapper) })

    // 转换标签渲染为 chip
    expect(wrapper.findAll('.json-fields__transform')).toHaveLength(1)
    expect(wrapper.text()).toContain('去除 HTML')

    // 移除转换
    const remove = wrapper.find('.json-fields__transform-remove')
    await remove.trigger('click')
    expect(lastMapping(wrapper).record_types[0].fields[1].transforms).toEqual([])
  })

  it('only shows transforms compatible with the inferred type', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 角色下拉会同步渲染其选项列表（面板在 body）；assert 转换添加下拉的选项内容
    // $.body（string）：含文本类转换，不含数组专属 join/deduplicate
    const bodyOptions = addDropdownOptions('$.body')
    expect(bodyOptions).toContain('去除首尾空白')
    expect(bodyOptions).toContain('去除 HTML')
    expect(bodyOptions).not.toContain('数组合并为文本')
    expect(bodyOptions).not.toContain('去重')

    // $.id（number）：只含标量转换，无文本类
    const idOptions = addDropdownOptions('$.id')
    expect(idOptions).toContain('转字符串')
    expect(idOptions).toContain('转布尔')
    expect(idOptions).not.toContain('去除首尾空白')
    expect(idOptions).not.toContain('解析日期')
  })

  it('transform catalog respects inferred type compatibility', () => {
    // 纯函数断言：string 有文本类转换、无 join；array 有 join；number 仅标量
    const stringOnes = allowedTransformsFor('string', [])
    expect(stringOnes).toContain('trim')
    expect(stringOnes).toContain('strip_html')
    expect(stringOnes).not.toContain('join')
    expect(stringOnes).not.toContain('deduplicate')

    const arrayOnes = allowedTransformsFor('array', [])
    expect(arrayOnes).toContain('join')
    expect(arrayOnes).toContain('deduplicate')

    const numberOnes = allowedTransformsFor('number', [])
    expect(numberOnes).toEqual(['to_string', 'to_boolean'])

    // 已应用的转换不再出现在候选中
    expect(allowedTransformsFor('string', ['trim'])).not.toContain('trim')
  })

  it('Continue is disabled until each indexed record type has title/content', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 当前 $.body 为 title：满足要求
    expect((wrapper.vm as unknown as { canContinue: boolean }).canContinue).toBe(true)

    // 全部字段改为 keyword/display：不再满足
    const onlyKeyword: MappingDefinition = {
      ...mapping,
      record_types: [
        {
          ...mapping.record_types[0],
          fields: [
            { path: '$.id', role: 'keyword', name: 'id', transforms: [], required: true },
            { path: '$.body', role: 'display', name: 'body', transforms: [], required: false },
          ],
        },
      ],
    }
    const w2 = mountStep(onlyKeyword)
    await flushPromises()
    expect((w2.vm as unknown as { canContinue: boolean }).canContinue).toBe(false)
    expect(w2.text()).toContain('至少需要一个 title 或 content 字段')
  })

  it('an ignore-chunked record type is exempt from the title/content requirement', async () => {
    const ignore: MappingDefinition = {
      ...mapping,
      record_types: [
        {
          ...mapping.record_types[0],
          chunk_policy: 'ignore',
          fields: [
            { path: '$.id', role: 'keyword', name: 'id', transforms: [], required: true },
            { path: '$.body', role: 'display', name: 'body', transforms: [], required: false },
          ],
        },
      ],
    }
    const wrapper = mountStep(ignore)
    await flushPromises()
    expect((wrapper.vm as unknown as { canContinue: boolean }).canContinue).toBe(true)
  })
})
