import { describe, expect, it } from 'vitest'
import { citationIndexFromClick, renderChatMarkdown } from './chatCitations'

describe('renderChatMarkdown', () => {
  it('renders canonical and malformed citations as bounded buttons', () => {
    const html = renderChatMarkdown('A[1] B[1][2] C[1# D[1##2#', 2)
    expect(html.match(/data-reference-index="0"/g)).toHaveLength(4)
    expect(html.match(/data-reference-index="1"/g)).toHaveLength(2)
    expect(html).not.toContain('[1#')
    expect(html).not.toContain('##')
  })

  it('keeps missing references and protected Markdown as text', () => {
    const html = renderChatMarkdown('`[1]` [link [1]](https://example.com/[1]) [3]', 2)
    expect(html).toContain('<code>[1]</code>')
    expect(html).toContain('href="https://example.com/%5B1%5D"')
    expect(html).toContain('[3]')
    expect(html).not.toContain('data-reference-index="2"')
  })

  it('does not transform fenced code', () => {
    expect(renderChatMarkdown('```text\n[1]\n```', 1)).not.toContain(
      'data-reference-index',
    )
  })
})

describe('citationIndexFromClick', () => {
  it('returns a validated zero-based index only for a contained citation button', () => {
    const root = document.createElement('div')
    root.innerHTML = '<button data-reference-index="1"><span>source</span></button>'
    const child = root.querySelector('span')!
    expect(
      citationIndexFromClick(
        { target: child, currentTarget: root } as unknown as MouseEvent,
        2,
      ),
    ).toBe(1)
    expect(
      citationIndexFromClick(
        { target: child, currentTarget: root } as unknown as MouseEvent,
        1,
      ),
    ).toBeNull()
  })
})
