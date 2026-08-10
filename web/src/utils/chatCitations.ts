import MarkdownIt from 'markdown-it'

interface CitationEnvironment {
  referenceCount?: number
}

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

md.core.ruler.after('inline', 'chat_citations', (state) => {
  const referenceCount = Number((state.env as CitationEnvironment).referenceCount)

  for (const blockToken of state.tokens) {
    if (blockToken.type !== 'inline' || !blockToken.children) continue

    const output = []
    let linkDepth = 0

    for (const token of blockToken.children) {
      if (token.type === 'link_open') {
        linkDepth += 1
        output.push(token)
        continue
      }
      if (token.type === 'link_close') {
        linkDepth = Math.max(0, linkDepth - 1)
        output.push(token)
        continue
      }
      if (
        token.type !== 'text' ||
        linkDepth > 0 ||
        /<\/?[A-Za-z][^>]*>/.test(token.content)
      ) {
        output.push(token)
        continue
      }

      let textStart = 0
      let position = 0
      let transformed = false
      while (position < token.content.length) {
        if (token.content.charCodeAt(position) !== 0x5b) {
          position += 1
          continue
        }

        const source = token.content.slice(position)
        const match = /^\[(\d+)\]/.exec(source) ?? /^\[(\d+(?:##\d+)*)#\]?/.exec(source)
        if (!match) {
          position += 1
          continue
        }

        const ids = match[1].split('##').map(Number)
        const valid =
          Number.isInteger(referenceCount) &&
          ids.every((id) => Number.isInteger(id) && id >= 1 && id <= referenceCount)
        if (!valid) {
          position += match[0].length
          continue
        }

        if (position > textStart) {
          const textToken = new state.Token('text', '', 0)
          textToken.content = token.content.slice(textStart, position)
          output.push(textToken)
        }
        for (const id of ids) {
          const citationToken = new state.Token('chat_citation', '', 0)
          citationToken.meta = { referenceIndex: id - 1 }
          output.push(citationToken)
        }
        transformed = true
        position += match[0].length
        textStart = position
      }

      if (!transformed) {
        output.push(token)
      } else if (textStart < token.content.length) {
        const textToken = new state.Token('text', '', 0)
        textToken.content = token.content.slice(textStart)
        output.push(textToken)
      }
    }

    blockToken.children = output
  }
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

  const rawIndex = button.getAttribute('data-reference-index')
  if (rawIndex === null || !/^(0|[1-9]\d*)$/.test(rawIndex)) return null

  const index = Number(rawIndex)
  return Number.isInteger(index) && index >= 0 && index < referenceCount ? index : null
}
