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

  it('preserves Markdown links, images, and disabled HTML as protected content', () => {
    const directLink = renderChatMarkdown('[1](https://example.com)', 1)
    expect(directLink).toContain('<a href="https://example.com">1</a>')
    expect(directLink).not.toContain('chat-view__inline-cite')

    const nestedLink = renderChatMarkdown(
      '[link [1]](https://example.com/[1])',
      1,
    )
    expect(nestedLink).toContain(
      '<a href="https://example.com/%5B1%5D">link [1]</a>',
    )
    expect(nestedLink).not.toContain('chat-view__inline-cite')

    const image = renderChatMarkdown('![alt [1]](image.png)', 1)
    expect(image).toContain('<img src="image.png" alt="alt [1]">')
    expect(image).not.toContain('chat-view__inline-cite')

    const disabledHtml = renderChatMarkdown('<span>[1]</span>', 1)
    expect(disabledHtml).toContain('&lt;span&gt;[1]&lt;/span&gt;')
    expect(disabledHtml).not.toContain('chat-view__inline-cite')
  })

  it('transforms citations outside paired HTML-like elements only', () => {
    const html = renderChatMarkdown(
      'Before [1] <span>[2]</span> after [3]',
      3,
    )

    expect(html).toContain('data-reference-index="0"')
    expect(html).not.toContain('data-reference-index="1"')
    expect(html).toContain('data-reference-index="2"')
    expect(html).toContain('&lt;span&gt;[2]&lt;/span&gt;')
  })

  it.each([
    ['<!-- [1] --> [2]', '&lt;!-- [1] --&gt;'],
    ['<!DOCTYPE note [1]> [2]', '&lt;!DOCTYPE note [1]&gt;'],
    ['<?target [1]?> [2]', '&lt;?target [1]?&gt;'],
    ['<![CDATA[[1]]]> [2]', '&lt;![CDATA[[1]]]&gt;'],
  ])('protects HTML-like construct in %j', (content, protectedHtml) => {
    const html = renderChatMarkdown(content, 2)

    expect(html).toContain(protectedHtml)
    expect(html).not.toContain('data-reference-index="0"')
    expect(html).toContain('data-reference-index="1"')
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

  it.each(['', ' ', '0.5', '0e0'])(
    'rejects a non-canonical reference index attribute %j',
    (value) => {
      const root = document.createElement('div')
      root.innerHTML = `<button data-reference-index="${value}">source</button>`
      const button = root.querySelector('button')!

      expect(
        citationIndexFromClick(
          { target: button, currentTarget: root } as unknown as MouseEvent,
          2,
        ),
      ).toBeNull()
    },
  )
})
