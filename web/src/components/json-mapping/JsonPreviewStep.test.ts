import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import JsonPreviewStep from './JsonPreviewStep.vue'
import type { PreviewRow } from '../../types/structured'

const rows: PreviewRow[] = [
  {
    record_id: 'post-1',
    parent_id: null,
    record_type: 'record',
    title: '',
    content: '免（退）增值税普及课的正文内容',
    embedding_text: '免（退）增值税普及课的正文内容',
    lexical: { title: '', content: '免（退）增值税普及课的正文内容', keywords: [] },
    filters: {},
    timestamps: {},
    display: {},
    raw: { body: '免（退）增值税普及课的正文内容' },
    source_pointer: '$',
    warnings: [],
  },
  {
    record_id: 'comment-1',
    parent_id: 'post-1',
    record_type: 'comment',
    title: '',
    content: '广州、杭州的100个名额有没有跑通？',
    embedding_text: '广州、杭州的100个名额有没有跑通？',
    lexical: { title: '', content: '广州、杭州的100个名额有没有跑通？', keywords: [] },
    filters: {},
    timestamps: {},
    display: {},
    raw: { author: 'yyy', text: '广州、杭州的100个名额有没有跑通？' },
    source_pointer: '$/comments/0',
    warnings: [],
  },
]

describe('JsonPreviewStep record picker', () => {
  it('distinguishes preview count from total and renders readable option labels', () => {
    const wrapper = mount(JsonPreviewStep, {
      props: { rows, totalRows: 10, globalWarnings: [], error: null },
      global: {
        stubs: {
          ElSelect: { template: '<div class="select"><slot /></div>' },
          ElOption: {
            props: ['label'],
            template: '<div class="option">{{ label }}</div>',
          },
          ElTabs: { template: '<div><slot /></div>' },
          ElTabPane: { template: '<div><slot /></div>' },
        },
      },
    })

    expect(wrapper.text()).toContain('预览 2')
    expect(wrapper.text()).toContain('共 10 条')
    const labels = wrapper.findAll('.option').map((option) => option.text())
    expect(labels[0]).toContain('主记录')
    expect(labels[0]).toContain('免（退）增值税普及课')
    expect(labels[1]).toContain('评论')
    expect(labels[1]).toContain('yyy：广州、杭州')
    expect(labels[1]).toContain('$.comments[0]')
    expect(labels[1]).not.toContain('$/comments/0')
  })
})
