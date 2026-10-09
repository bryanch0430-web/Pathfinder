import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  ApiError,
  deletePlanItem,
  getSession,
  openStream,
  patchPlanItem,
  postMessage,
  type StreamHandle,
  type TripPlan,
  type TurnEvent,
  type TurnResult,
} from '@/api'
import { makePlan } from '@/test/fixtures'
import { usePlanStore } from '../plan'
import { useSessionStore } from '../session'

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  openStream: vi.fn(),
  postMessage: vi.fn(),
  getSession: vi.fn(),
  patchPlanItem: vi.fn(),
  deletePlanItem: vi.fn(),
}))

function turnResult(plan: TripPlan | null = null): TurnResult {
  return {
    session_id: 'session-123',
    trace_id: 'trace-1',
    route: 'modify',
    gate: { route: 'modify', reason: 'accepted' },
    reply: 'Done.',
    plan,
    plan_changed: plan !== null,
    input_flagged: false,
    agents_run: [],
  }
}

function event(partial: Partial<TurnEvent> & Pick<TurnEvent, 'type'>): TurnEvent {
  return { trace_id: '', route: null, agent: null, status: null, message: null, result: null, ...partial }
}

interface FakeStream {
  handle: StreamHandle
  send: ReturnType<typeof vi.fn>
  emit: (e: TurnEvent) => void
}

/** Make openStream return an open socket whose events the test drives. */
function fakeStream(): FakeStream {
  const send = vi.fn()
  const handle: StreamHandle = { ready: Promise.resolve(), state: 'open', send, close: vi.fn() }
  const fake: FakeStream = { handle, send, emit: () => {} }
  vi.mocked(openStream).mockImplementation((_id, onEvent) => {
    fake.emit = onEvent
    return handle
  })
  return fake
}

/** Make the stream unavailable so send() falls back to POST /messages. */
function noStream(): void {
  vi.mocked(openStream).mockImplementation(() => {
    const ready = Promise.reject(new ApiError(0, 'no socket'))
    ready.catch(() => {})
    return { ready, state: 'closed', send: vi.fn(), close: vi.fn() }
  })
}

describe('session store: plan workspace', () => {
  let plan: TripPlan

  beforeEach(() => {
    setActivePinia(createPinia())
    plan = makePlan()
    vi.mocked(getSession).mockReset().mockResolvedValue({
      session_id: 'session-123',
      context: {},
      plan,
      history: [],
      preferences: {},
    })
    vi.mocked(postMessage).mockReset()
    vi.mocked(patchPlanItem).mockReset()
    vi.mocked(deletePlanItem).mockReset()
    usePlanStore().setPlan(plan)
    useSessionStore().sessionId = 'session-123'
  })

  it('sends the selection as focus on the stream frame', async () => {
    const stream = fakeStream()
    const planStore = usePlanStore()
    const session = useSessionStore()
    planStore.select('item', 'kiyomizu@1')
    const sent = session.send('swap for a museum')
    await vi.waitFor(() => expect(stream.send).toHaveBeenCalled())
    expect(stream.send).toHaveBeenCalledWith('swap for a museum', { kind: 'item', id: 'kiyomizu@1' })
    stream.emit(event({ type: 'done', trace_id: 'trace-1', result: turnResult() }))
    await expect(sent).resolves.toEqual({ ok: true, draft: null })
    expect(planStore.selection).toEqual({ kind: 'item', id: 'kiyomizu@1' })
  })

  it('sends no focus without a selection', async () => {
    const stream = fakeStream()
    const session = useSessionStore()
    const sent = session.send('hello')
    await vi.waitFor(() => expect(stream.send).toHaveBeenCalled())
    expect(stream.send).toHaveBeenCalledWith('hello', null)
    stream.emit(event({ type: 'done', trace_id: 'trace-1', result: turnResult() }))
    await sent
  })

  it('sends the focus over POST /messages when the stream is unavailable', async () => {
    noStream()
    vi.mocked(postMessage).mockResolvedValue(turnResult())
    usePlanStore().select('day', '2026-11-11')
    await expect(useSessionStore().send('something calmer')).resolves.toEqual({ ok: true, draft: null })
    expect(postMessage).toHaveBeenCalledWith('session-123', 'something calmer', { kind: 'day', id: '2026-11-11' })
  })

  it('clears the selection and gives the text back when POST refuses the focus', async () => {
    noStream()
    const detail = "focus item 'kiyomizu@1' is not in the current plan"
    vi.mocked(postMessage).mockRejectedValue(new ApiError(422, detail))
    const planStore = usePlanStore()
    const session = useSessionStore()
    planStore.select('item', 'kiyomizu@1')
    await expect(session.send('  swap for a museum ')).resolves.toEqual({ ok: false, draft: '  swap for a museum ' })
    expect(planStore.selection).toBeNull()
    expect(session.log.map((e) => [e.role, e.kind, e.content])).toEqual([['assistant', 'error', detail]])
  })

  it('treats a refused stream frame for a focused message the same way', async () => {
    const stream = fakeStream()
    const planStore = usePlanStore()
    const session = useSessionStore()
    planStore.select('item', 'gion@2')
    const sent = session.send('something cheaper')
    await vi.waitFor(() => expect(stream.send).toHaveBeenCalled())
    stream.emit(event({ type: 'error', message: 'session has no plan' }))
    await expect(sent).resolves.toEqual({ ok: false, draft: 'something cheaper' })
    expect(planStore.selection).toBeNull()
    expect(session.log.map((e) => [e.role, e.content])).toEqual([['assistant', 'session has no plan']])
  })

  it('keeps today\'s chat error for an unfocused failure', async () => {
    noStream()
    vi.mocked(postMessage).mockRejectedValue(new ApiError(422, 'message: too long'))
    const session = useSessionStore()
    await expect(session.send('hi')).resolves.toEqual({ ok: false, draft: null })
    expect(session.log.map((e) => [e.role, e.content])).toEqual([
      ['user', 'hi'],
      ['assistant', 'message: too long'],
    ])
  })

  it('edits a stop through the API and applies the returned plan', async () => {
    const edited = makePlan({ version: 2 })
    vi.mocked(patchPlanItem).mockResolvedValue(edited)
    const session = useSessionStore()
    await expect(session.editItem('kiyomizu@1', { start_time: '10:30' })).resolves.toEqual({ ok: true })
    expect(patchPlanItem).toHaveBeenCalledWith('session-123', 'kiyomizu@1', { start_time: '10:30' })
    expect(usePlanStore().plan).toEqual(edited)
    expect(session.editingItemId).toBeNull()
  })

  it('returns the server message and leaves the plan alone when an edit fails', async () => {
    vi.mocked(patchPlanItem).mockRejectedValue(new ApiError(422, 'end_time 09:00 must be after start_time 10:00'))
    const session = useSessionStore()
    await expect(session.editItem('kiyomizu@1', { end_time: '09:00' })).resolves.toEqual({
      ok: false,
      error: 'end_time 09:00 must be after start_time 10:00',
    })
    expect(usePlanStore().plan).toEqual(plan)
    expect(session.lastError).toBeNull()
  })

  it('deleting the selected stop clears the selection', async () => {
    const day1 = plan.days[0]!
    const without = makePlan({
      version: 2,
      days: [{ ...day1, items: day1.items!.filter((i) => i.item_id !== 'kiyomizu@1') }, plan.days[1]!],
    })
    vi.mocked(deletePlanItem).mockResolvedValue(without)
    const planStore = usePlanStore()
    planStore.select('item', 'kiyomizu@1')
    await expect(useSessionStore().deleteItem('kiyomizu@1')).resolves.toEqual({ ok: true })
    expect(deletePlanItem).toHaveBeenCalledWith('session-123', 'kiyomizu@1')
    expect(planStore.plan).toEqual(without)
    expect(planStore.selection).toBeNull()
  })

  it('refuses a manual edit while a turn is running', async () => {
    const session = useSessionStore()
    session.busy = true
    const result = await session.deleteItem('kiyomizu@1')
    expect(result.ok).toBe(false)
    expect(deletePlanItem).not.toHaveBeenCalled()
  })
})
