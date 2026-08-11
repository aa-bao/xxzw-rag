import MarkdownIt from 'markdown-it'

interface CitationEnvironment {
  referenceCount?: number
}

interface TextSegment {
  content: string
  protected: boolean
}

interface HtmlTag {
  end: number
  name: string
  closing: boolean
  selfClosing: boolean
}

const VOID_HTML_TAGS = new Set([
  'area',
  'base',
  'br',
  'col',
  'embed',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'param',
  'source',
  'track',
  'wbr',
])

function terminatedMarkupEnd(content: string, start: number, terminator: string): number {
  const end = content.indexOf(terminator, start)
  return end === -1 ? content.length : end + terminator.length
}

function tagEnd(content: string, start: number): number {
  let quote = ''
  let bracketDepth = 0
  for (let index = start + 1; index < content.length; index += 1) {
    const character = content[index]
    if (quote) {
      if (character === quote) quote = ''
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
    } else if (character === '[') {
      bracketDepth += 1
    } else if (character === ']') {
      bracketDepth = Math.max(0, bracketDepth - 1)
    } else if (character === '>' && bracketDepth === 0) {
      return index + 1
    }
  }
  return content.length
}

function htmlTagAt(content: string, start: number): HtmlTag | null {
  const match = /^<\/?([A-Za-z][A-Za-z0-9:-]*)(?=[\s/>])/.exec(content.slice(start))
  if (!match) return null

  const end = tagEnd(content, start)
  const source = content.slice(start, end)
  return {
    end,
    name: match[1].toLowerCase(),
    closing: source.startsWith('</'),
    selfClosing: /\/\s*>$/.test(source),
  }
}

function specialMarkupEnd(content: string, start: number): number | null {
  if (content.startsWith('<!--', start)) {
    return terminatedMarkupEnd(content, start + 4, '-->')
  }
  if (content.startsWith('<![CDATA[', start)) {
    return terminatedMarkupEnd(content, start + 9, ']]>')
  }
  if (content.startsWith('<?', start)) {
    return terminatedMarkupEnd(content, start + 2, '?>')
  }
  if (content.startsWith('<!', start)) return tagEnd(content, start)
  return null
}

function pairedElementEnd(
  content: string,
  openingStart: number,
  openingTag: HtmlTag,
): number {
  let depth = 1
  let cursor = openingTag.end
  while (cursor < content.length) {
    const start = content.indexOf('<', cursor)
    if (start === -1) break

    const specialEnd = specialMarkupEnd(content, start)
    if (specialEnd !== null) {
      cursor = specialEnd
      continue
    }

    const tag = htmlTagAt(content, start)
    if (!tag) {
      cursor = start + 1
      continue
    }
    if (tag.name === openingTag.name) {
      if (tag.closing) {
        depth -= 1
        if (depth === 0) return tag.end
      } else if (!tag.selfClosing) {
        depth += 1
      }
    }
    cursor = tag.end
  }
  return htmlTagAt(content, openingStart)?.end ?? openingTag.end
}

function protectedHtmlEnd(content: string, start: number): number | null {
  const specialEnd = specialMarkupEnd(content, start)
  if (specialEnd !== null) return specialEnd

  const tag = htmlTagAt(content, start)
  if (!tag) return null
  if (tag.closing || tag.selfClosing || VOID_HTML_TAGS.has(tag.name)) return tag.end
  return pairedElementEnd(content, start, tag)
}

function splitHtmlLikeSegments(content: string): TextSegment[] {
  const segments: TextSegment[] = []
  let plainStart = 0
  let cursor = 0
  while (cursor < content.length) {
    const protectedStart = content.indexOf('<', cursor)
    if (protectedStart === -1) break

    const protectedEnd = protectedHtmlEnd(content, protectedStart)
    if (protectedEnd === null) {
      cursor = protectedStart + 1
      continue
    }
    if (protectedStart > plainStart) {
      segments.push({ content: content.slice(plainStart, protectedStart), protected: false })
    }
    segments.push({ content: content.slice(protectedStart, protectedEnd), protected: true })
    plainStart = protectedEnd
    cursor = protectedEnd
  }
  if (plainStart < content.length) {
    segments.push({ content: content.slice(plainStart), protected: false })
  }
  return segments
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
        linkDepth > 0
      ) {
        output.push(token)
        continue
      }

      for (const segment of splitHtmlLikeSegments(token.content)) {
        if (segment.protected) {
          const textToken = new state.Token('text', '', 0)
          textToken.content = segment.content
          output.push(textToken)
          continue
        }

        let textStart = 0
        let position = 0
        let transformed = false
        while (position < segment.content.length) {
          if (segment.content.charCodeAt(position) !== 0x5b) {
            position += 1
            continue
          }

          const source = segment.content.slice(position)
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
            textToken.content = segment.content.slice(textStart, position)
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
          const textToken = new state.Token('text', '', 0)
          textToken.content = segment.content
          output.push(textToken)
        } else if (textStart < segment.content.length) {
          const textToken = new state.Token('text', '', 0)
          textToken.content = segment.content.slice(textStart)
          output.push(textToken)
        }
      }
    }

    blockToken.children = output
  }
})

md.renderer.rules.chat_citation = (tokens, index) => {
  const referenceIndex = Number(tokens[index].meta.referenceIndex)
  return `<button type="button" class="chat-view__inline-cite" data-reference-index="${referenceIndex}" aria-label="查看资料来源 ${referenceIndex + 1}">${referenceIndex + 1}</button>`
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
