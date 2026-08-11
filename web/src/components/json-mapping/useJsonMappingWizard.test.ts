import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { SourceProfile, MappingDefinition, PreviewRow } from '../../types/structured'
import { useJsonMappingWizard } from './useJsonMappingWizard'

const KB_ID = 7

function makeProfile(overrides: Partial<SourceProfile> = {}): SourceProfile {
  return {
    source_format: 'json',
    doc_id: 42,
    candidates: [
      {
        record_path: '$',
        record_count_estimate: 10,
        field_count: 3,
        nested_array_count: 1,
        confidence: 0.9,
        samples: ['{redacted}', '{redacted}'],
        fields: [],
        suggested_mapping: makeMapping(),
      },
    ],
    fingerprint: 'fp-1',
    total_records_estimate: 10,
    sampled_records: 10,
    warnings: [],
    ...overrides,
  }
}

function makeMapping(overrides: Partial<MappingDefinition> = {}): MappingDefinition {
  return {
    source_format: 'json',
    record_types: [
      {
        name: 'post',
        record_path: '$',
        fields: [
          { path: '$.title', role: 'title', name: 'title', transforms: [], required: true },
          { path: '$.body', role: 'content', name: 'body', transforms: [], required: true },
        ],
        children: [],
        chunk_policy: 'semantic',
        relation_rule: null,
      },
    ],
    ...overrides,
  }
}

function makeRows(): PreviewRow[] {
  return [
    {
      record_id: 'r1',
      parent_id: null,
      record_type: 'post',
      title: 't',
      content: 'c',
      embedding_text: 't c',
      lexical: { title: 't', keywords: [], content: 'c' },
      filters: {},
      timestamps: {},
      display: {},
      raw: { title: 't', body: 'c' },
      source_pointer: '/0',
      warnings: [],
    },
  ]
}

describe('useJsonMappingWizard', () => {
  let hook: ReturnType<typeof useJsonMappingWizard>

  beforeEach(() => {
    vi.clearAllMocks()
    hook = useJsonMappingWizard(KB_ID)
  })

  it('starts at the upload step with no file', () => {
    expect(hook.state.value.step).toBe('upload')
    expect(hook.busy.value).toBeNull()
    if (hook.state.value.step === 'upload') {
      expect(hook.state.value.file).toBeNull()
    }
  })

  it('advances to structure after a successful profile', async () => {
    const profile = makeProfile()
    const profileJsonSpy = vi.spyOn(hook.api, 'profileJson').mockResolvedValue(profile)
    hook.setFile(new File(['{}'], 'posts.json'))

    await hook.profile(42)

    expect(profileJsonSpy).toHaveBeenCalledWith(KB_ID, 42)
    expect(hook.busy.value).toBeNull()
    expect(hook.state.value.step).toBe('structure')
    if (hook.state.value.step === 'structure') {
      expect(hook.state.value.docId).toBe(42)
      expect(hook.state.value.profile).toStrictEqual(profile)
    }
  })

  it('keeps busy set to the operation name during profile and clears it after', async () => {
    let release: (p: SourceProfile) => void = () => {}
    vi.spyOn(hook.api, 'profileJson').mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    )
    hook.setFile(new File(['{}'], 'posts.json'))

    const pending = hook.profile(42)
    expect(hook.busy.value).toBe('profile')

    release(makeProfile())
    await pending
    expect(hook.busy.value).toBeNull()
  })

  it('guards fields transition: mapping without a title or content field cannot enter fields', () => {
    const profile = makeProfile()
    // 替换候选草稿：两条字段都不是 title/content，且记录类型为语义切块（需索引）
    const mapping = makeMapping({
      record_types: [
        {
          name: 'post',
          record_path: '$',
          fields: [
            { path: '$.a', role: 'keyword', name: 'a', transforms: [], required: false },
            { path: '$.b', role: 'display', name: 'b', transforms: [], required: false },
          ],
          children: [],
          chunk_policy: 'semantic',
          relation_rule: null,
        },
      ],
    })

    expect(() => hook.selectCandidate(profile, mapping)).not.toThrow()
    // 没有 title/content 的映射不能进入 fields
    expect(() => hook.next()).toThrow(/title|content/)
  })

  it('guards preview: cannot preview while a profile is absent', async () => {
    // 仍处于 upload 步骤（无 profile）时直接调用 preview 必须失败
    await expect(hook.preview(makeMapping())).rejects.toThrow(/profile|结构/)
  })

  it('invalidates confirmation when a breaking schema change is detected', async () => {
    const profile = makeProfile()
    vi.spyOn(hook.api, 'profileJson').mockResolvedValue(profile)
    hook.setFile(new File(['{}'], 'posts.json'))
    await hook.profile(42)

    const mapping = makeMapping()
    hook.selectCandidate(profile, mapping)
    hook.next() // -> fields

    const rows = makeRows()
    vi.spyOn(hook.api, 'previewJson').mockResolvedValue({
      rows,
      warnings: [],
      total_rows: rows.length,
      limit: null,
    })
    await hook.preview(mapping)

    expect(hook.state.value.step).toBe('preview')

    // 破坏性 schema 变更：移除 content 字段（映射更新使确认失效）
    const breaking = makeMapping({
      record_types: [
        {
          name: 'post',
          record_path: '$',
          fields: [
            { path: '$.title', role: 'title', name: 'title', transforms: [], required: true },
          ],
          children: [],
          chunk_policy: 'semantic',
          relation_rule: null,
        },
      ],
    })
    hook.applyMappingUpdate(breaking)
    // 确认被失效：不能进入 confirm，必须重新预览
    expect(() => hook.next()).toThrow(/重新预览/)
    expect(hook.state.value.step).toBe('preview')

    // 返回 relations 步骤重新预览后恢复：可以进入 confirm
    hook.back()
    expect(hook.state.value.step).toBe('relations')
    vi.spyOn(hook.api, 'previewJson').mockResolvedValue({
      rows: [],
      warnings: [],
      total_rows: 0,
      limit: null,
    })
    await hook.preview(breaking)
    hook.next()
    expect(hook.state.value.step).toBe('confirm')
  })

  it('records doc_id and job_id on ingest success and resets transient preview rows', async () => {
    const profile = makeProfile()
    vi.spyOn(hook.api, 'profileJson').mockResolvedValue(profile)
    hook.setFile(new File(['{}'], 'posts.json'))
    await hook.profile(42)

    const mapping = makeMapping()
    hook.selectCandidate(profile, mapping)
    hook.next() // -> fields

    const rows = makeRows()
    vi.spyOn(hook.api, 'previewJson').mockResolvedValue({
      rows,
      warnings: [],
      total_rows: rows.length,
      limit: null,
    })
    await hook.preview(mapping)
    hook.next() // -> confirm

    vi.spyOn(hook.api, 'startJsonIngest').mockResolvedValue({
      doc_id: 42,
      job_id: 99,
      mapping_version_id: 3,
      status: 'queued',
    })

    await hook.confirmIngest(7)

    expect(hook.ingested.value).toEqual({ docId: 42, jobId: 99 })
    // transient 数据重置：回到 upload，无文件、无预览行
    expect(hook.state.value.step).toBe('upload')
    if (hook.state.value.step === 'upload') {
      expect(hook.state.value.file).toBeNull()
    }
  })

  it('prevents double submits while an operation is running', async () => {
    const profile = makeProfile()
    let release: (p: SourceProfile) => void = () => {}
    vi.spyOn(hook.api, 'profileJson').mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    )
    hook.setFile(new File(['{}'], 'posts.json'))

    const first = hook.profile(42)
    await expect(hook.profile(42)).rejects.toThrow(/busy|进行中/)
    // busy 恢复后可以再次执行
    release(profile)
    await first
    expect(hook.busy.value).toBeNull()
    hook.setFile(new File(['{}'], 'again.json'))
    const second = hook.profile(43)
    release(profile)
    await second
    expect(hook.state.value.step).toBe('structure')
  })

  it('backs to the previous step and resets transient data of forward steps', async () => {
    const profile = makeProfile()
    vi.spyOn(hook.api, 'profileJson').mockResolvedValue(profile)
    hook.setFile(new File(['{}'], 'posts.json'))
    await hook.profile(42)
    expect(hook.state.value.step).toBe('structure')

    hook.back()
    expect(hook.state.value.step).toBe('upload')
  })
})
