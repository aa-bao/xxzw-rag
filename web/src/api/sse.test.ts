import { describe, it, expect } from 'vitest'
import { streamSse } from '../api/sse'

describe('streamSse', () => {
  it('parses event/data pairs from a stream', async () => {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          'event: chunk\ndata: {"content":"hello"}\n\nevent: done\ndata: {"done":true}\n\n'
        ))
        controller.close()
      },
    })
    const resp = new Response(body)

    const events = []
    for await (const ev of streamSse(resp)) {
      events.push(ev)
    }

    expect(events).toHaveLength(2)
    expect(events[0]).toEqual({ event: 'chunk', data: '{"content":"hello"}' })
    expect(events[1]).toEqual({ event: 'done', data: '{"done":true}' })
  })
})
