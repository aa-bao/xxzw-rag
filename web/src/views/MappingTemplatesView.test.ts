import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElDialog } from 'element-plus'
import MappingTemplatesView from './MappingTemplatesView.vue'
import type { MappingDefinition, MappingTemplateSummary } from '../types/structured'

vi.mock('../api/structured', () => ({
  listMappingTemplates: vi.fn(),
  createMappingTemplateVersion: vi.fn(),
}))

const { listMappingTemplates, createMappingTemplateVersion } = await import('../api/structured')
const listTemplatesMock = vi.mocked(listMappingTemplates)
const createVersionMock = vi.mocked(createMappingTemplateVersion)

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
  id: 3,
  name: '博客文章',
  source_format: 'json',
  current_version: 2,
  fingerprint: 'fp-current',
  usage_count: 5,
  created_at: '2026-01-02T03:04:05Z',
  updated_at: '2026-02-03T04:05:06Z',
  versions: [
    { version: 1, mapping, fingerprint: 'fp-v1', usage_count: 2, created_at: '2026-01-02T03:04:05Z', created_by: 'alice' },
    { version: 2, mapping, fingerprint: 'fp-current', usage_count: 3, created_at: '2026-02-03T04:05:06Z', created_by: 'alice' },
  ],
}

type ViewWrapper = VueWrapper<unknown>

function mountView(templates: MappingTemplateSummary[] = [template]): ViewWrapper {
  listTemplatesMock.mockResolvedValue(templates)
  return mount(MappingTemplatesView, { global: { plugins: [ElementPlus] } })
}

function buttonsByText(wrapper: ViewWrapper, text: string): ReturnType<ViewWrapper['findAll']> {
  return wrapper.findAll('button').filter((b) => b.text().includes(text))
}

/** 与视图一致的本地时区格式化（视图用 Date 本地时区渲染） */
function fmt(iso: string): string {
  const d = new Date(iso)
  const pad2 = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MappingTemplatesView', () => {
  it('renders template list with name, source format, version, fingerprint, usage count and timestamps', async () => {
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('博客文章')
    expect(text).toContain('JSON')
    expect(text).toContain('v2')
    expect(text).toContain('fp-current'.slice(0, 12))
    expect(text).toContain('5')
    expect(text).toContain(fmt('2026-01-02T03:04:05Z'))
    expect(text).toContain(fmt('2026-02-03T04:05:06Z'))
  })

  it('renders an empty state when no templates exist', async () => {
    const wrapper = mountView([])
    await flushPromises()

    expect(wrapper.text()).toContain('还没有映射模板')
  })

  it('shows an error state when loading fails', async () => {
    listTemplatesMock.mockRejectedValue({ error: { code: 'ERR', message: '服务器错误' } })
    const wrapper = mount(MappingTemplatesView, { global: { plugins: [ElementPlus] } })
    await flushPromises()

    expect(wrapper.text()).toContain('模板加载失败')
    expect(wrapper.text()).toContain('服务器错误')
  })

  it('expanding a version shows immutable mapping JSON and the field-role summary', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 展开 v1
    await buttonsByText(wrapper, 'v1')[0].trigger('click')
    await flushPromises()

    const text = wrapper.text()
    // 字段角色摘要（记录类型 / 策略 / 角色）
    expect(text).toContain('post')
    expect(text).toContain('comment')
    expect(text).toContain('语义切块')
    expect(text).toContain('并入父记录')
    expect(text).toContain('ID/正文')
    // 只读映射 JSON
    expect(text).toContain('"record_types"')
    expect(text).toContain('comments[*]')
  })

  it('creates a new version from the latest version and keeps used versions intact', async () => {
    createVersionMock.mockResolvedValue({ template_id: 3, mapping_version_id: 99, version: 3, fingerprint: 'fp-v3' })
    const wrapper = mountView()
    await flushPromises()

    // 从最新版本创建 → 打开编辑对话框
    await buttonsByText(wrapper, '从最新版本创建')[0].trigger('click')
    await flushPromises()

    // 编辑框基于 v2 展示（只读基线提示 + 复用字段/关系编辑器）
    const dialogText = wrapper.text()
    expect(dialogText).toContain('v2')
    expect(dialogText).toContain('创建新版本')
    expect(dialogText).toContain('保存新版本')

    await buttonsByText(wrapper, '保存新版本')[0].trigger('click')
    await flushPromises()

    // 新版本提交到模板，而非新建模板（name 缺省 + 使用原 template_id）
    expect(createVersionMock).toHaveBeenCalledWith(
      expect.objectContaining({ template_id: 3, mapping: expect.any(Object) }),
    )
    // 保存成功后刷新列表
    expect(listTemplatesMock).toHaveBeenCalled()
  })

  it('keeps the save disabled until the mapping has a title/content for every indexed record type', async () => {
    const missingContent: MappingDefinition = {
      source_format: 'json',
      record_types: [
        {
          name: 'post',
          record_path: '$',
          chunk_policy: 'semantic',
          relation_rule: null,
          fields: [{ path: '$.id', role: 'id', name: 'id', transforms: [], required: true }],
          children: [],
        },
      ],
    }
    const tpl: MappingTemplateSummary = { ...template, versions: [{ ...template.versions[0], mapping: missingContent }] }
    const wrapper = mountView([tpl])
    await flushPromises()

    await buttonsByText(wrapper, '从最新版本创建')[0].trigger('click')
    await flushPromises()

    const save = buttonsByText(wrapper, '保存新版本')
    expect(save).toHaveLength(1)
    expect((save[0].element as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows a success message and reloads after saving a new version', async () => {
    createVersionMock.mockResolvedValue({ template_id: 3, mapping_version_id: 100, version: 4, fingerprint: 'fp-v4' })
    listTemplatesMock.mockResolvedValueOnce([template]).mockResolvedValueOnce([template])
    const wrapper = mountView()
    await flushPromises()

    await buttonsByText(wrapper, '从最新版本创建')[0].trigger('click')
    await flushPromises()
    await buttonsByText(wrapper, '保存新版本')[0].trigger('click')
    await flushPromises()

    // 成功后关闭编辑对话框并刷新列表
    expect(wrapper.findComponent(ElDialog).props('modelValue')).toBe(false)
    expect(listTemplatesMock).toHaveBeenCalledTimes(2)
  })
})
