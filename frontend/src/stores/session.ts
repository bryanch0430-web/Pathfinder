/**
 * Session store: the conversation, the route status of the turn in flight, the trip context and
 * the session lifecycle. The TripPlan itself lives in the plan store (`./plan`); this store
 * writes it there after each turn.
 *
 * All backend access goes through `@/api`; components only read the stores and call actions.
 * A turn prefers the WebSocket stream (live route + per-agent progress) and falls back to the
 * plain POST /messages endpoint when the socket cannot be used.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  ApiError,
  confirmItems,
  createSession,
  deletePlanItem,
  getHealth,
  getSession,
  openStream,
  patchPlanItem,
  postMessage,
  sendFeedback,
  updateContext,
} from '@/api'
import type {
  AgentName,
  Clarification,
  ConfirmInput,
  GateReason,
  ConversationTurn,
  HealthResponse,
  PlanFocus,
  PlanItemPatch,
  PreferenceProfile,
  QuickAnswer,
  Route,
  SectionStatus,
  SessionView,
  StreamHandle,
  TripContext,
  TripPlan,
  TurnEvent,
  TurnResult,
} from '@/api'
import { AGENT_NAMES } from '@/lib/constants'
import { missingKeyVariables } from '@/lib/context'
import { usePlanStore } from './plan'

const STORAGE_KEY = 'pathfinder.sessionId'

// ---- public shapes -----------------------------------------------------------------------------

export type EntryKind = 'message' | 'clarification' | 'error'

export interface ChatEntry {
  id: number
  role: 'user' | 'assistant'
  kind: EntryKind
  content: string
  route: Route | null
  at: string | null
  /** Names of context fields the backend still needs (clarification replies only). */
  missingFields: string[]
  /** The user message this assistant entry answers (used to resend after a clarification). */
  prompt: string | null
  /** Why the gate asked for clarification (clarification entries only). */
  gateReason: GateReason | null
  /** Structured quick answer for the "ask" route, when one was produced. */
  answer: QuickAnswer | null
  /** The input was flagged by the injection screen (it is still handled as plain data). */
  flagged: boolean
  agentsRun: AgentName[]
}

export interface AgentProgress {
  phase: 'idle' | 'running' | 'finished'
  status: SectionStatus | null
  note: string | null
}

export interface FeedbackOutcome {
  planId: string
  version: number
  useful: boolean
  rating: number
  queued: boolean
}

/**
 * What `send()` did. `draft` is the text to put back in the composer when the message was not
 * taken (the server refused its focus, or there was no session); otherwise null.
 */
export interface SendResult {
  ok: boolean
  draft: string | null
}

/** Options for `send()`. */
export interface SendOptions {
  /**
   * Send the workspace selection as the message's `focus`. Only messages the user types in the
   * chat composer opt in; refresh badges, budget and clarification shortcuts and Generate never do.
   */
  focus?: boolean
}

/** Outcome of a manual stop edit; `error` is the server message to show next to the control. */
export interface PlanEditResult {
  ok: boolean
  error?: string
}

export type BackendState =
  | { online: null }
  | { online: true; info: HealthResponse }
  | { online: false; message: string }

// ---- internals ---------------------------------------------------------------------------------

/** The socket could not be opened, or closed before the server said anything: safe to fall back to HTTP. */
class StreamUnavailableError extends Error {}
/** The socket dropped after the server had started the turn: the turn may have run, do not resend. */
class StreamInterruptedError extends Error {
  constructor() {
    super('The live connection was lost during the turn. The reply may still have been saved; the session was reloaded.')
  }
}
/** The server refused the message before starting a turn (invalid message, unknown session). */
class StreamRejectedError extends Error {}
/** The refusal was the pre-turn "error" event (empty trace id), e.g. a focus not in the plan. */
class StreamRefusedError extends StreamRejectedError {}

interface PendingTurn {
  resolve: (result: TurnResult) => void
  reject: (error: unknown) => void
  sawEvent: boolean
}

function readStoredSessionId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredSessionId(sessionId: string | null): void {
  try {
    if (sessionId) window.localStorage.setItem(STORAGE_KEY, sessionId)
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage can be blocked (private window, policy); the session just will not survive a reload.
  }
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.detail
  if (error instanceof Error) return error.message
  return String(error)
}

/** The server refused a focused message's focus: no plan (409) or a part not in the plan (422). */
function isFocusRefusal(error: unknown): boolean {
  if (error instanceof StreamRefusedError) return true
  return error instanceof ApiError && (error.isConflict || error.status === 422)
}

function idleProgress(): Record<AgentName, AgentProgress> {
  const progress = {} as Record<AgentName, AgentProgress>
  for (const agent of AGENT_NAMES) progress[agent] = { phase: 'idle', status: null, note: null }
  return progress
}

// ---- store -------------------------------------------------------------------------------------

export const useSessionStore = defineStore('session', () => {
  // Session data
  const sessionId = ref<string | null>(null)
  const planStore = usePlanStore()
  const context = ref<TripContext>({})
  const preferences = ref<PreferenceProfile | null>(null)
  const log = ref<ChatEntry[]>([])

  // Turn state
  const busy = ref(false)
  const currentRoute = ref<Route | null>(null)
  /** Reply text of the turn in flight, as soon as the stream delivers it (before "done"). */
  const liveReply = ref<string | null>(null)
  const agents = ref<Record<AgentName, AgentProgress>>(idleProgress())
  const transport = ref<'stream' | 'http' | null>(null)

  // Misc UI state
  const loading = ref(false)
  const savingContext = ref(false)
  const confirming = ref(false)
  const sendingFeedback = ref(false)
  /** The stop whose manual edit (PATCH/DELETE) is in flight. */
  const editingItemId = ref<string | null>(null)
  const feedbackOutcome = ref<FeedbackOutcome | null>(null)
  const lastError = ref<string | null>(null)
  const sessionLost = ref(false)
  const backend = ref<BackendState>({ online: null })

  // Non-reactive plumbing
  let stream: StreamHandle | null = null
  let pending: PendingTurn | null = null
  let initPromise: Promise<void> | null = null
  let nextEntryId = 1

  const hasPlan = computed(() => planStore.plan !== null)
  const missingVariables = computed(() => missingKeyVariables(context.value))
  const agentsActive = computed(() => AGENT_NAMES.some((agent) => agents.value[agent].phase !== 'idle'))

  // ---- log helpers -----------------------------------------------------------------------------

  /** Add one chat entry and return its id. */
  function appendEntry(entry: Partial<ChatEntry> & Pick<ChatEntry, 'role' | 'content'>): number {
    const id = nextEntryId++
    log.value.push({
      id,
      kind: 'message',
      route: null,
      at: null,
      missingFields: [],
      prompt: null,
      gateReason: null,
      answer: null,
      flagged: false,
      agentsRun: [],
      ...entry,
    })
    return id
  }

  function entriesFromHistory(history: ConversationTurn[]): void {
    log.value = []
    let lastUserText: string | null = null
    for (const turn of history) {
      appendEntry({
        role: turn.role,
        content: turn.content,
        route: turn.route ?? null,
        at: turn.at ?? null,
        kind: turn.role === 'assistant' && turn.route === 'unclear' ? 'clarification' : 'message',
        prompt: turn.role === 'assistant' ? lastUserText : null,
        // History does not keep the gate's missing fields; recompute them from the context so a
        // restored clarification still gets its inline inputs.
        missingFields: turn.role === 'assistant' && turn.route === 'unclear' ? missingVariables.value : [],
      })
      if (turn.role === 'user') lastUserText = turn.content
    }
  }

  function resetProgress(): void {
    agents.value = idleProgress()
    currentRoute.value = null
    liveReply.value = null
  }

  // ---- session lifecycle -----------------------------------------------------------------------

  function applySession(view: SessionView, options: { replaceLog: boolean }): void {
    sessionId.value = view.session_id
    context.value = view.context
    planStore.setPlan(view.plan)
    preferences.value = view.preferences
    if (options.replaceLog) entriesFromHistory(view.history)
    writeStoredSessionId(view.session_id)
  }

  function markSessionLost(): void {
    sessionLost.value = true
    lastError.value =
      'This session no longer exists on the server (it may have been restarted). Start a new session to continue.'
  }

  function reportError(error: unknown): void {
    if (error instanceof ApiError && error.isNotFound) {
      markSessionLost()
      return
    }
    lastError.value = describeError(error)
  }

  async function initialise(): Promise<void> {
    loading.value = true
    try {
      const stored = readStoredSessionId()
      if (stored) {
        try {
          applySession(await getSession(stored), { replaceLog: true })
          return
        } catch (error) {
          // A 404 means the stored session is gone: start over. Anything else (backend down) is
          // surfaced and the stored id is kept for the next attempt.
          if (!(error instanceof ApiError && error.isNotFound)) throw error
        }
      }
      applySession(await createSession({}), { replaceLog: true })
    } catch (error) {
      reportError(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  /** Load the stored session or create one. Safe to call repeatedly and concurrently. */
  function ensureSession(): Promise<void> {
    if (sessionId.value) return Promise.resolve()
    if (!initPromise) {
      initPromise = initialise().finally(() => {
        initPromise = null
      })
    }
    return initPromise
  }

  /** Re-read context, preferences and plan from the server (the chat log is kept as is). */
  async function refreshSession(): Promise<void> {
    const id = sessionId.value
    if (!id) return
    try {
      const view = await getSession(id)
      context.value = view.context
      preferences.value = view.preferences
      if (view.plan) planStore.setPlan(view.plan)
    } catch (error) {
      if (error instanceof ApiError && error.isNotFound) markSessionLost()
    }
  }

  /** Drop the current session (and its stored id) and start a fresh one. */
  async function newSession(): Promise<void> {
    if (busy.value) return
    closeStream()
    writeStoredSessionId(null)
    sessionId.value = null
    context.value = {}
    planStore.setPlan(null)
    preferences.value = null
    log.value = []
    feedbackOutcome.value = null
    lastError.value = null
    sessionLost.value = false
    transport.value = null
    resetProgress()
    try {
      await ensureSession()
    } catch {
      // ensureSession already recorded the error.
    }
  }

  async function checkHealth(): Promise<void> {
    try {
      backend.value = { online: true, info: await getHealth() }
    } catch (error) {
      backend.value = { online: false, message: describeError(error) }
    }
  }

  // ---- context ---------------------------------------------------------------------------------

  async function saveContext(next: TripContext): Promise<boolean> {
    savingContext.value = true
    lastError.value = null
    try {
      await ensureSession()
      const id = sessionId.value
      if (!id) return false
      const view = await updateContext(id, next)
      context.value = view.context
      preferences.value = view.preferences
      if (view.plan) planStore.setPlan(view.plan)
      return true
    } catch (error) {
      reportError(error)
      return false
    } finally {
      savingContext.value = false
    }
  }

  // ---- chat ------------------------------------------------------------------------------------

  function closeStream(): void {
    const handle = stream
    stream = null
    handle?.close()
    if (pending) {
      const turn = pending
      pending = null
      turn.reject(new StreamUnavailableError('stream closed'))
    }
  }

  function handleStreamClosed(handle: StreamHandle): void {
    if (stream !== handle) return
    stream = null
    if (pending) {
      const turn = pending
      pending = null
      turn.reject(turn.sawEvent ? new StreamInterruptedError() : new StreamUnavailableError('stream closed'))
    }
  }

  function handleStreamEvent(event: TurnEvent): void {
    const turn = pending
    if (!turn) return
    turn.sawEvent = true
    switch (event.type) {
      case 'route':
        currentRoute.value = event.route
        break
      case 'agent_started':
        if (event.agent) agents.value[event.agent] = { phase: 'running', status: null, note: null }
        break
      case 'agent_finished':
        if (event.agent) {
          agents.value[event.agent] = { phase: 'finished', status: event.status, note: event.message }
        }
        break
      case 'plan':
      case 'answer':
      case 'clarification':
        // The reply text arrives here, before "done" delivers the full result.
        liveReply.value = event.message
        break
      case 'error':
        liveReply.value = event.message
        // An error without a trace id means the server refused the frame before starting a
        // turn, so no "done" will follow. Errors inside a turn are followed by "done".
        if (!event.trace_id) {
          pending = null
          turn.reject(new StreamRefusedError(event.message ?? 'The server rejected the message'))
        }
        break
      case 'done':
        pending = null
        if (event.result) turn.resolve(event.result)
        else turn.reject(new StreamRejectedError('The turn ended without a result'))
        break
      default:
        break
    }
  }

  async function ensureStream(): Promise<StreamHandle> {
    const id = sessionId.value
    if (!id) throw new StreamUnavailableError('no session')
    if (stream && stream.state !== 'closed') {
      await stream.ready.catch(() => {
        throw new StreamUnavailableError('stream not ready')
      })
      return stream
    }
    const handle: StreamHandle = openStream(id, handleStreamEvent, {
      onClose: () => handleStreamClosed(handle),
    })
    stream = handle
    try {
      await handle.ready
    } catch {
      if (stream === handle) stream = null
      handle.close()
      throw new StreamUnavailableError('stream could not be opened')
    }
    return handle
  }

  async function runOnStream(message: string, focus: PlanFocus | null): Promise<TurnResult> {
    const handle = await ensureStream()
    return new Promise<TurnResult>((resolve, reject) => {
      pending = { resolve, reject, sawEvent: false }
      try {
        handle.send(message, focus)
      } catch {
        pending = null
        reject(new StreamUnavailableError('send failed'))
      }
    })
  }

  function applyResult(result: TurnResult, prompt: string): void {
    currentRoute.value = result.route

    // Over plain HTTP there are no live events, so settle the chips from the result itself.
    for (const agent of result.agents_run ?? []) {
      if (agents.value[agent].phase !== 'finished') {
        const section = result.plan?.sections?.find((s) => s.agent === agent)
        agents.value[agent] = {
          phase: 'finished',
          status: section?.status ?? null,
          note: section?.reason ?? null,
        }
      }
    }

    const clarification: Clarification | null = result.clarification ?? null
    let kind: EntryKind = 'message'
    if (result.error) kind = 'error'
    else if (clarification || result.route === 'unclear') kind = 'clarification'

    appendEntry({
      role: 'assistant',
      kind,
      content: result.reply || result.error?.message || '',
      route: result.route,
      at: new Date().toISOString(),
      missingFields: clarification?.missing_fields ?? result.gate.missing_fields ?? [],
      prompt,
      gateReason: kind === 'clarification' ? clarification?.reason ?? result.gate.reason : null,
      answer: result.answer ?? null,
      flagged: result.input_flagged,
      agentsRun: result.agents_run ?? [],
    })

    if (result.plan) planStore.setPlan(result.plan)
  }

  /**
   * Send one chat message. Prefers the WebSocket stream; if the socket cannot be used before the
   * server has said anything, the same message is sent once through POST /messages instead.
   *
   * With `focus: true` (the chat composer only), the plan store's selection, if any, goes with the
   * message as its `focus`. When the server refuses that focus (stale selection), the selection is
   * cleared, the server message is shown in the chat and the text comes back in `draft` so the
   * composer can restore it.
   */
  async function send(text: string, options: SendOptions = {}): Promise<SendResult> {
    const message = text.trim()
    if (!message) return { ok: false, draft: null }
    if (busy.value) return { ok: false, draft: text }
    lastError.value = null
    try {
      await ensureSession()
    } catch {
      return { ok: false, draft: text }
    }
    const id = sessionId.value
    if (!id) return { ok: false, draft: text }

    const focus: PlanFocus | null = options.focus && planStore.selection ? { ...planStore.selection } : null
    const userEntryId = appendEntry({ role: 'user', content: message, at: new Date().toISOString() })
    busy.value = true
    resetProgress()

    try {
      let result: TurnResult
      try {
        result = await runOnStream(message, focus)
        transport.value = 'stream'
      } catch (error) {
        if (!(error instanceof StreamUnavailableError)) throw error
        transport.value = 'http'
        result = await postMessage(id, message, focus)
      }
      applyResult(result, message)
      await refreshSession()
      return { ok: true, draft: null }
    } catch (error) {
      if (focus && isFocusRefusal(error)) {
        // The turn never ran (the server keeps no history for it): the text goes back to the composer.
        log.value = log.value.filter((entry) => entry.id !== userEntryId)
        planStore.clearSelection()
        appendEntry({ role: 'assistant', kind: 'error', content: describeError(error) })
        await refreshSession()
        return { ok: false, draft: text }
      }
      // A failed turn is reported in the chat itself; only a vanished session gets the banner.
      if (error instanceof ApiError && error.isNotFound) markSessionLost()
      else appendEntry({ role: 'assistant', kind: 'error', content: describeError(error) })
      if (error instanceof StreamInterruptedError || error instanceof StreamRejectedError) {
        await refreshSession()
      }
      return { ok: false, draft: null }
    } finally {
      busy.value = false
    }
  }

  // ---- plan actions ----------------------------------------------------------------------------

  async function confirm(input: ConfirmInput): Promise<boolean> {
    const id = sessionId.value
    if (!id || !planStore.plan) return false
    confirming.value = true
    lastError.value = null
    try {
      planStore.setPlan(await confirmItems(id, input))
      return true
    } catch (error) {
      reportError(error)
      // 422 (ids no longer in the plan) or 409 (no plan): the local copy is stale.
      if (error instanceof ApiError && (error.status === 422 || error.isConflict)) await refreshSession()
      return false
    } finally {
      confirming.value = false
    }
  }

  const setItemConfirmed = (itemId: string, confirmed: boolean): Promise<boolean> =>
    confirm({ item_ids: [itemId], confirmed })

  const setTicketConfirmed = (ticketId: string, confirmed: boolean): Promise<boolean> =>
    confirm({ ticket_ids: [ticketId], confirmed })

  const setHotelConfirmed = (confirmed: boolean): Promise<boolean> => confirm({ hotel: true, confirmed })

  /**
   * Run one manual stop edit and apply the plan the server returns (never optimistic). Errors are
   * returned for an inline message rather than shown in the banner.
   */
  async function editStop(itemId: string, call: (id: string) => Promise<TripPlan>): Promise<PlanEditResult> {
    const id = sessionId.value
    if (!id || !planStore.plan) return { ok: false, error: 'There is no plan to edit yet.' }
    if (busy.value) return { ok: false, error: 'Wait for the current reply to finish.' }
    if (editingItemId.value) return { ok: false, error: 'Another change is still being saved.' }
    editingItemId.value = itemId
    try {
      planStore.setPlan(await call(id))
      return { ok: true }
    } catch (error) {
      if (error instanceof ApiError && error.isNotFound) markSessionLost()
      return { ok: false, error: describeError(error) }
    } finally {
      editingItemId.value = null
    }
  }

  /** PATCH one stop: times, note (null clears it) or day. Send only the fields that change. */
  const editItem = (itemId: string, patch: PlanItemPatch): Promise<PlanEditResult> =>
    editStop(itemId, (id) => patchPlanItem(id, itemId, patch))

  /** DELETE one stop. A selection on it clears with the new plan. */
  const deleteItem = (itemId: string): Promise<PlanEditResult> =>
    editStop(itemId, (id) => deletePlanItem(id, itemId))

  async function submitFeedback(useful: boolean, rating: number): Promise<boolean> {
    const id = sessionId.value
    const current = planStore.plan
    if (!id || !current) return false
    sendingFeedback.value = true
    lastError.value = null
    try {
      const response = await sendFeedback(id, { useful, rating })
      feedbackOutcome.value = {
        planId: current.plan_id,
        version: current.version,
        useful,
        rating,
        queued: response.queued,
      }
      return true
    } catch (error) {
      reportError(error)
      return false
    } finally {
      sendingFeedback.value = false
    }
  }

  function dismissError(): void {
    lastError.value = null
  }

  return {
    // state
    sessionId,
    context,
    preferences,
    log,
    busy,
    currentRoute,
    liveReply,
    agents,
    transport,
    loading,
    savingContext,
    confirming,
    sendingFeedback,
    editingItemId,
    feedbackOutcome,
    lastError,
    sessionLost,
    backend,
    // getters
    hasPlan,
    missingVariables,
    agentsActive,
    // actions
    ensureSession,
    refreshSession,
    newSession,
    checkHealth,
    saveContext,
    send,
    confirm,
    setItemConfirmed,
    setTicketConfirmed,
    setHotelConfirmed,
    editItem,
    deleteItem,
    submitFeedback,
    dismissError,
    closeStream,
  }
})
