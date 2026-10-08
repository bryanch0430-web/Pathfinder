/**
 * Session store: the single source of truth for the console.
 *
 * All backend access goes through `@/api`; components only read this store and call its actions.
 * A turn prefers the WebSocket stream (live route + per-agent progress) and falls back to the
 * plain POST /messages endpoint when the socket cannot be used.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  ApiError,
  confirmItems,
  createSession,
  getHealth,
  getSession,
  openStream,
  postMessage,
  sendFeedback,
  updateContext,
} from '@/api'
import type {
  AgentName,
  Clarification,
  ConfirmInput,
  ConversationTurn,
  HealthResponse,
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

function idleProgress(): Record<AgentName, AgentProgress> {
  const progress = {} as Record<AgentName, AgentProgress>
  for (const agent of AGENT_NAMES) progress[agent] = { phase: 'idle', status: null, note: null }
  return progress
}

// ---- store -------------------------------------------------------------------------------------

export const useSessionStore = defineStore('session', () => {
  // Session data
  const sessionId = ref<string | null>(null)
  const context = ref<TripContext>({})
  const plan = ref<TripPlan | null>(null)
  const preferences = ref<PreferenceProfile | null>(null)
  const log = ref<ChatEntry[]>([])

  // Turn state
  const busy = ref(false)
  const currentRoute = ref<Route | null>(null)
  const agents = ref<Record<AgentName, AgentProgress>>(idleProgress())
  const transport = ref<'stream' | 'http' | null>(null)

  // Misc UI state
  const loading = ref(false)
  const savingContext = ref(false)
  const confirming = ref(false)
  const sendingFeedback = ref(false)
  const feedbackOutcome = ref<FeedbackOutcome | null>(null)
  const lastError = ref<string | null>(null)
  const sessionLost = ref(false)
  const backend = ref<BackendState>({ online: null })

  // Non-reactive plumbing
  let stream: StreamHandle | null = null
  let pending: PendingTurn | null = null
  let initPromise: Promise<void> | null = null
  let nextEntryId = 1

  const hasPlan = computed(() => plan.value !== null)
  const missingVariables = computed(() => missingKeyVariables(context.value))
  const agentsActive = computed(() => AGENT_NAMES.some((agent) => agents.value[agent].phase !== 'idle'))

  // ---- log helpers -----------------------------------------------------------------------------

  function appendEntry(entry: Partial<ChatEntry> & Pick<ChatEntry, 'role' | 'content'>): void {
    log.value.push({
      id: nextEntryId++,
      kind: 'message',
      route: null,
      at: null,
      missingFields: [],
      answer: null,
      flagged: false,
      agentsRun: [],
      ...entry,
    })
  }

  function entriesFromHistory(history: ConversationTurn[]): void {
    log.value = []
    for (const turn of history) {
      appendEntry({
        role: turn.role,
        content: turn.content,
        route: turn.route ?? null,
        at: turn.at ?? null,
        kind: turn.role === 'assistant' && turn.route === 'unclear' ? 'clarification' : 'message',
      })
    }
  }

  function resetProgress(): void {
    agents.value = idleProgress()
    currentRoute.value = null
  }

  // ---- session lifecycle -----------------------------------------------------------------------

  function applySession(view: SessionView, options: { replaceLog: boolean }): void {
    sessionId.value = view.session_id
    context.value = view.context
    plan.value = view.plan
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
      if (view.plan) plan.value = view.plan
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
    plan.value = null
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
      if (view.plan) plan.value = view.plan
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
      case 'error':
        // An error without a trace id means the server refused the frame before starting a
        // turn, so no "done" will follow. Errors inside a turn are followed by "done".
        if (!event.trace_id) {
          pending = null
          turn.reject(new StreamRejectedError(event.message ?? 'The server rejected the message'))
        }
        break
      case 'done':
        pending = null
        if (event.result) turn.resolve(event.result)
        else turn.reject(new StreamRejectedError('The turn ended without a result'))
        break
      default:
        // plan / answer / clarification carry the same reply text that "done" delivers in full.
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

  async function runOnStream(message: string): Promise<TurnResult> {
    const handle = await ensureStream()
    return new Promise<TurnResult>((resolve, reject) => {
      pending = { resolve, reject, sawEvent: false }
      try {
        handle.send(message)
      } catch {
        pending = null
        reject(new StreamUnavailableError('send failed'))
      }
    })
  }

  function applyResult(result: TurnResult): void {
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
      answer: result.answer ?? null,
      flagged: result.input_flagged,
      agentsRun: result.agents_run ?? [],
    })

    if (result.plan) plan.value = result.plan
  }

  /**
   * Send one chat message. Prefers the WebSocket stream; if the socket cannot be used before the
   * server has said anything, the same message is sent once through POST /messages instead.
   */
  async function send(text: string): Promise<void> {
    const message = text.trim()
    if (!message || busy.value) return
    lastError.value = null
    try {
      await ensureSession()
    } catch {
      return
    }
    const id = sessionId.value
    if (!id) return

    appendEntry({ role: 'user', content: message, at: new Date().toISOString() })
    busy.value = true
    resetProgress()

    try {
      let result: TurnResult
      try {
        result = await runOnStream(message)
        transport.value = 'stream'
      } catch (error) {
        if (!(error instanceof StreamUnavailableError)) throw error
        transport.value = 'http'
        result = await postMessage(id, message)
      }
      applyResult(result)
      await refreshSession()
    } catch (error) {
      // A failed turn is reported in the chat itself; only a vanished session gets the banner.
      if (error instanceof ApiError && error.isNotFound) markSessionLost()
      else appendEntry({ role: 'assistant', kind: 'error', content: describeError(error) })
      if (error instanceof StreamInterruptedError || error instanceof StreamRejectedError) {
        await refreshSession()
      }
    } finally {
      busy.value = false
    }
  }

  // ---- plan actions ----------------------------------------------------------------------------

  async function confirm(input: ConfirmInput): Promise<boolean> {
    const id = sessionId.value
    if (!id || !plan.value) return false
    confirming.value = true
    lastError.value = null
    try {
      plan.value = await confirmItems(id, input)
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

  async function submitFeedback(useful: boolean, rating: number): Promise<boolean> {
    const id = sessionId.value
    const current = plan.value
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
    plan,
    preferences,
    log,
    busy,
    currentRoute,
    agents,
    transport,
    loading,
    savingContext,
    confirming,
    sendingFeedback,
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
    submitFeedback,
    dismissError,
    closeStream,
  }
})
