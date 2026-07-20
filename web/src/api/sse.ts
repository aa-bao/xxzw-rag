export interface SseEvent {
  event: string
  data: string
}

export async function* streamSse(
  response: Response,
): AsyncIterable<SseEvent> {
  const reader = response.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    let event = ''
    let data = ''
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        event = line.slice(7)
      } else if (line.startsWith('data: ')) {
        data = line.slice(6)
      } else if (line === '') {
        if (event && data) {
          yield { event, data }
          event = ''
          data = ''
        }
      }
    }
  }
}
