import { describe, it, expect, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElSelect, ElSwitch } from 'element-plus'
import JsonRelationStep from './JsonRelationStep.vue'
import type { MappingDefinition } from '../../types/structured'

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
        { path: '$.title', role: 'title', name: 'title', transforms: [], required: false },
      ],
      children: [
        {
          name: 'comment',
          record_path: 'comments[*]',
          chunk_policy: 'parent-only',
          relation_rule: null,
          fields: [
            { path: 'comments[*].id', role: 'id', name: 'id', transforms: [], required: true },
            { path: 'comments[*].body', role: 'content', name: 'body', transforms: [], required: false },
            { path: 'comments[*].referTo', role: 'filter', name: 'referTo', transforms: [], required: false },
            { path: 'comments[*].author', role: 'filter', name: 'author', transforms: [], required: false },
          ],
          children: [],
        },
      ],
    },
  ],
}

type StepWrapper = VueWrapper<unknown>

const mountStep = (m = mapping): StepWrapper =>
  mount(JsonRelationStep, {
    props: { mapping: m },
    global: { plugins: [ElementPlus] },
  })

function lastMapping(wrapper: StepWrapper): MappingDefinition {
  const emitted = wrapper.emitted('update:mapping')!
  return emitted[emitted.length - 1][0] as MappingDefinition
}

/** 切换某节点上的 ElSwitch（0 = 父 post，1 = 子 comment） */
async function toggleSwitch(wrapper: StepWrapper, index: number) {
  const sw = wrapper.findAllComponents(ElSwitch)[index]
  const on = (sw.props('modelValue') as boolean) === true
  sw.vm.$emit('update:model-value', !on)
  await flushPromises()
}

/** 下拉（策略 + 关系源/目标）按文档顺序 */
function selects(wrapper: StepWrapper) {
  return wrapper.findAllComponents(ElSelect)
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('JsonRelationStep', () => {
  it('renders the record-type tree with chunk policies', async () => {
    const wrapper = mountStep()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('post')
    expect(text).toContain('$')
    expect(text).toContain('语义切块')
    expect(text).toContain('comment')
    expect(text).toContain('comments[*]')
    expect(text).toContain('并入父记录')
  })

  it('changes the chunk policy via update:mapping', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 第一个下拉 = post 的切块策略：改为原子切块
    selects(wrapper)[0].vm.$emit('update:model-value', 'atomic')
    expect(lastMapping(wrapper).record_types[0].chunk_policy).toBe('atomic')
    // 子记录不受影响
    expect(lastMapping(wrapper).record_types[0].children[0].chunk_policy).toBe('parent-only')
  })

  it('enables the nearest-previous-sibling rule with dropdowns and sets referTo→author', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 打开子记录 comment 的关系规则（受控组件：回喂新映射后再查 DOM）
    await toggleSwitch(wrapper, 1)
    await wrapper.setProps({ mapping: lastMapping(wrapper) })

    let updated = lastMapping(wrapper)
    const comment = updated.record_types[0].children[0]
    // 默认取本记录类型前两个可用字段（id、body）
    expect(comment.relation_rule).toEqual({
      source: 'comments[*].id',
      target: 'comments[*].body',
      strategy: 'nearest_previous_sibling',
    })

    // 源字段选 referTo，目标字段选 author（仅下拉，无表达式输入）；
    // 受控组件：每次 emit 后回喂新映射，下一次 emit 才能基于最新状态
    const ruleSelects = selects(wrapper).slice(2, 4)
    ruleSelects[0].vm.$emit('update:model-value', 'comments[*].referTo')
    await wrapper.setProps({ mapping: lastMapping(wrapper) })
    ruleSelects[1].vm.$emit('update:model-value', 'comments[*].author')
    await wrapper.setProps({ mapping: lastMapping(wrapper) })

    updated = lastMapping(wrapper)
    expect(updated.record_types[0].children[0].relation_rule).toEqual({
      source: 'comments[*].referTo',
      target: 'comments[*].author',
      strategy: 'nearest_previous_sibling',
    })
  })

  it('disabling the rule removes only relation_rule', async () => {
    const withRule: MappingDefinition = {
      ...mapping,
      record_types: [
        {
          ...mapping.record_types[0],
          children: [
            {
              ...mapping.record_types[0].children[0],
              relation_rule: {
                source: 'comments[*].referTo',
                target: 'comments[*].author',
                strategy: 'nearest_previous_sibling',
              },
            },
          ],
        },
      ],
    }
    const wrapper = mountStep(withRule)
    await flushPromises()

    // 关闭 comment 的关系规则
    await toggleSwitch(wrapper, 1)

    const updated = lastMapping(wrapper)
    const comment = updated.record_types[0].children[0]
    expect(comment.relation_rule).toBeNull()
    // 其余配置原样保留
    expect(comment.chunk_policy).toBe('parent-only')
    expect(comment.fields).toEqual(withRule.record_types[0].children[0].fields)
    expect(comment.record_path).toBe('comments[*]')
  })

  it('shows parent ID inheritance and ignore behavior hints', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // comment 的父记录 post 有 id 字段：显示父记录 ID 继承提示
    expect(wrapper.text()).toContain('父记录 ID 自动写入')
    expect(wrapper.text()).toContain('$.id')

    // 把 comment 策略改为 ignore（受控组件：发出 update:mapping 并回喂新映射）
    selects(wrapper)[1].vm.$emit('update:model-value', 'ignore')
    await wrapper.setProps({ mapping: lastMapping(wrapper) })
    expect(wrapper.text()).toContain('该记录类型不生成记录')
  })

  it('no free-text inputs exist in the rule editor', async () => {
    const wrapper = mountStep()
    await flushPromises()

    // 规则编辑器里只应有下拉与开关，不允许用户自由输入表达式
    // （ElSelect 内部是 readonly 的输入框，不算自由输入）
    const textInputs = wrapper
      .findAll('input[type="text"], textarea')
      .filter((i) => (i.element as HTMLInputElement).readOnly === false)
    expect(textInputs).toHaveLength(0)
  })
})

