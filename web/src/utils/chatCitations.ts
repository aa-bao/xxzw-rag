import MarkdownIt from 'markdown-it'

interface CitationEnvironment {
  referenceCount?: number
}

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

md.inline.ruler.before('emphasis', 'chat_citation', (state, silent) => {
  if (state.src.charCodeAt(state.pos) !== 0x5b || state.linkLevel !== 0) return false

  const source = state.src.slice(state.pos)
  const match = /^\[(\d+)\]/.exec(source) ?? /^\[(\d+(?:##\d+)*)#\]?/.exec(source)
  if (!match) return false

  const referenceCount = Number((state.env as CitationEnvironment).referenceCount)
  const ids = match[1].split('##').map(Number)
  if (
    !Number.isInteger(referenceCount) ||
    ids.some((id) => !Number.isInteger(id) || id < 1 || id > referenceCount)
  ) {
    return false
  }

  if (!silent) {
    for (const id of ids) {
      const token = state.push('chat_citation', '', 0)
      token.meta = { referenceIndex: id - 1 }
    }
  }
  state.pos += match[0].length
  return true
})

md.renderer.rules.chat_citation = (tokens, index) => {
  const referenceIndex = Number(tokens[index].meta.referenceIndex)
  return `<button type="button" class="chat-view__inline-cite" data-reference-index="${referenceIndex}" aria-label="查看资料来源 ${referenceIndex + 1}">[${referenceIndex + 1}]</button>`
}

export function renderChatMarkdown(content: string, referenceCount: number): string {
  const safeCount = Number.isInteger(referenceCount) && referenceCount > 0 ? referenceCount : 0
  return md.render(content || '', { referenceCount: safeCount })
}

export function citationIndexFromClick(
  event: MouseEvent,
  referenceCount: number,
): number | null {
  if (!(event.target instanceof Element) || !(event.currentTarget instanceof Element)) return null

  const button = event.target.closest('button[data-reference-index]')
  if (!button || !event.currentTarget.contains(button)) return null

  const index = Number(button.getAttribute('data-reference-index'))
  return Number.isInteger(index) && index >= 0 && index < referenceCount ? index : null
}
