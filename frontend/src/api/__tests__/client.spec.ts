import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, deletePlanItem, openStream, patchPlanItem, postMessage } from '@/api'
import { makePlan } from '@/test/fixtures'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('api client: plan workspace calls', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('PATCHes one stop and returns the plan', async () => {
    const plan = makePlan({ version: 2 })
    fetchMock.mockResolvedValue(jsonResponse(200, plan))
    await expect(patchPlanItem('s 1', 'kiyomizu@1', { start_time: '10:30', note: null })).resolves.toEqual(plan)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/sessions/s%201/plan/items/kiyomizu%401')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ start_time: '10:30', note: null })
  })

  it('DELETEs one stop and surfaces the server message on a 409', async () => {
    fetchMock.mockResolvedValue(jsonResponse(409, { detail: 'Fushimi Inari Taisha is locked: unlock it first' }))
    const error = await deletePlanItem('s1', 'inari@1').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).detail).toBe('Fushimi Inari Taisha is locked: unlock it first')
    const [url, init] = fetchMock.mock.calls[0]!
    expect([url, init.method, init.body]).toEqual(['/api/sessions/s1/plan/items/inari%401', 'DELETE', undefined])
  })

  it('posts the focus with a message only when one is given', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, {}))
    await postMessage('s1', 'swap for a museum', { kind: 'item', id: 'kiyomizu@1' })
    await postMessage('s1', 'hello')
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({
      message: 'swap for a museum',
      focus: { kind: 'item', id: 'kiyomizu@1' },
    })
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({ message: 'hello' })
  })
})

describe('api client: stream frames', () => {
  class FakeSocket extends EventTarget {
    static last: FakeSocket | null = null
    sent: string[] = []
    constructor(public url: string) {
      super()
      FakeSocket.last = this
    }
    send(data: string): void {
      this.sent.push(data)
    }
    close(): void {}
  }

  beforeEach(() => vi.stubGlobal('WebSocket', FakeSocket))
  afterEach(() => vi.unstubAllGlobals())

  it('sends the focus in the frame', async () => {
    const handle = openStream('s1', () => {})
    FakeSocket.last!.dispatchEvent(new Event('open'))
    await handle.ready
    handle.send('something cheaper', { kind: 'hotel', id: 'piece-sanjo' })
    handle.send('hello')
    expect(FakeSocket.last!.sent.map((frame) => JSON.parse(frame))).toEqual([
      { message: 'something cheaper', focus: { kind: 'hotel', id: 'piece-sanjo' } },
      { message: 'hello' },
    ])
    handle.close()
  })
})
