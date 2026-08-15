import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import JsonStructureStep from './JsonStructureStep.vue'
import type { ProfileCandidate, SourceProfile } from '../../types/structured'

const candidate = (recordPath: string): ProfileCandidate => ({
  record_path: recordPath,
  record_count_estimate: 1,
  field_count: 26,
  nested_array_count: 3,
  confidence: 1,
  samples: ['188111288418182'],
  fields: [],
  suggested_mapping: { source_format: 'json', record_types: [] },
})

const profile = (recordPath: string): SourceProfile => ({
  source_format: 'json',
  doc_id: 1,
  candidates: [candidate(recordPath)],
  fingerprint: 'fingerprint',
  total_records_estimate: 1,
  sampled_records: 1,
  warnings: [],
})

describe('JsonStructureStep', () => {
  it('explains the root JSONPath instead of showing a bare dollar sign', () => {
    const wrapper = mount(JsonStructureStep, {
      props: { profile: profile('$'), busy: null, error: '' },
    })

    const path = wrapper.get('.structure-card__path')
    expect(path.text()).toContain('根对象')
    expect(path.text()).toContain('$')
    expect(path.attributes('title')).toBe('$（根对象）')
  })

  it('keeps a nested candidate path unchanged', () => {
    const wrapper = mount(JsonStructureStep, {
      props: { profile: profile('$.comments[*]'), busy: null, error: '' },
    })

    const path = wrapper.get('.structure-card__path')
    expect(path.text()).toBe('$.comments[*]')
    expect(path.attributes('title')).toBe('$.comments[*]')
  })
})
