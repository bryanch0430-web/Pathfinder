/**
 * The ONE place the frontend talks to the backend.
 *
 * Nothing outside `src/api/` may call `fetch` or construct a `WebSocket`. Everything here is
 * typed from the generated OpenAPI types, so the HTTP/WebSocket contract is the only coupling
 * between the Vue app and the Python backend.
 */
import type {
  ConfirmInput,
  FeedbackRequest,
  FeedbackResponse,
  HealthResponse,
  SessionView,
  TripContext,
  TripPlan,
  TurnEvent,
  TurnEventType,
  TurnResult,
} from './types'

// ---- configuration -----------------------------------------------------------------------------

/** Empty string means "same origin" (the Vite dev server proxies /api to the backend). */
const API_BASE = (import.meta.env.VITE_API_BASE ?? '').trim().replace(/\/+$/, '')

/** How long to wait for the WebSocket handshake before treating the stream as unavailable. */
const STREAM_OPEN_TIMEOUT_MS = 4000

// ---- errors ------------------------------------------------------------------------------------

/**
 * Any failure talking to the API. `status` is the HTTP status, or 0 when the server could not be
 * reached (network error, closed socket). `detail` is the backend's `detail` message when it
 * sent one.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }

  /** 404: unknown session. */
  get isNotFound(): boolean {
    return this.status === 404
  }

  /** 409: the action needs a plan and the session has none yet. */
  get isConflict(): boolean {
    return this.status === 409
  }

  /** The server was never reached (offline, wrong URL, backend down). */
  get isNetwork(): boolean {
    return this.status === 0
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Extract a readable message from `{detail: string}` or FastAPI's `{detail: [{loc, msg}]}`. */
function describeErrorBody(body: unknown, fallback: string): string {
  if (!isRecord(body)) return fallback
  const detail = body.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((entry: unknown) => {
      if (!isRecord(entry) || typeof entry.msg !== 'string') return JSON.stringify(entry)
      const loc = Array.isArray(entry.loc)
        ? entry.loc.filter((part: unknown) => part !== 'body').join('.')
        : ''
      return loc ? `${loc}: ${entry.msg}` : entry.msg
    })
    if (parts.length > 0) return parts.join('; ')
  }
  return fallback
}

// ---- HTTP --------------------------------------------------------------------------------------

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    throw new ApiError(0, `Cannot reach the Pathfinder API (${reason})`)
  }

  if (!response.ok) {
    let parsed: unknown = null
    try {
      parsed = await response.json()
    } catch {
      // Non-JSON error body: typically a dev-proxy or gateway error because the backend is down.
    }
    const fallback =
      parsed === null
        ? `The API did not answer with a JSON error (HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}). Is the backend running?`
        : `HTTP ${response.status}`
    throw new ApiError(response.status, describeErrorBody(parsed, fallback))
  }

  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(response.status, 'The API returned a response that is not valid JSON')
  }
}

const sessionPath = (sessionId: string): string => `/api/sessions/${encodeURIComponent(sessionId)}`

/** GET /api/health */
export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('GET', '/api/health')
}

/** POST /api/sessions (201) */
export function createSession(context: TripContext = {}): Promise<SessionView> {
  return request<SessionView>('POST', '/api/sessions', { context })
}

/** GET /api/sessions/{id}; rejects with a 404 ApiError for an unknown session. */
export function getSession(sessionId: string): Promise<SessionView> {
  return request<SessionView>('GET', sessionPath(sessionId))
}

/** PUT /api/sessions/{id}/context */
export function updateContext(sessionId: string, context: TripContext): Promise<SessionView> {
  return request<SessionView>('PUT', `${sessionPath(sessionId)}/context`, { context })
}

/** POST /api/sessions/{id}/messages: one chat turn over plain HTTP (no live progress). */
export function postMessage(sessionId: string, message: string): Promise<TurnResult> {
  return request<TurnResult>('POST', `${sessionPath(sessionId)}/messages`, { message })
}

/**
 * POST /api/sessions/{id}/plan/confirm; 409 when the session has no plan yet.
 *
 * Contract quirk: `confirmed` applies to every id in one request, and `hotel` is a "target"
 * flag (send `hotel: true` to apply `confirmed` to the hotel stay).
 */
export function confirmItems(sessionId: string, input: ConfirmInput): Promise<TripPlan> {
  return request<TripPlan>('POST', `${sessionPath(sessionId)}/plan/confirm`, {
    item_ids: input.item_ids ?? [],
    ticket_ids: input.ticket_ids ?? [],
    hotel: input.hotel ?? null,
    confirmed: input.confirmed ?? true,
  })
}

/** POST /api/sessions/{id}/plan/feedback; 409 when the session has no plan yet. */
export function sendFeedback(
  sessionId: string,
  feedback: FeedbackRequest,
): Promise<FeedbackResponse> {
  return request<FeedbackResponse>('POST', `${sessionPath(sessionId)}/plan/feedback`, feedback)
}

// ---- WebSocket ---------------------------------------------------------------------------------

const TURN_EVENT_TYPES: ReadonlySet<string> = new Set<TurnEventType>([
  'route',
  'agent_started',
  'agent_finished',
  'plan',
  'answer',
  'clarification',
  'error',
  'done',
])

/** Build ws:// or wss:// from VITE_API_BASE, or from the page's own origin when it is empty. */
function streamUrl(sessionId: string): string {
  const path = `${sessionPath(sessionId)}/stream`
  if (API_BASE) {
    const url = new URL(`${API_BASE}${path}`, window.location.href)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    return url.toString()
  }
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}${path}`
}

/** Parse one frame; frames that are not a TurnEvent are dropped rather than trusted. */
function parseTurnEvent(raw: unknown): TurnEvent | null {
  if (typeof raw !== 'string') return null
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isRecord(value) || typeof value.type !== 'string' || !TURN_EVENT_TYPES.has(value.type)) {
    return null
  }
  return value as unknown as TurnEvent
}

export interface StreamCloseInfo {
  /** The close was requested by us through `close()`. */
  requested: boolean
  code: number
  reason: string
}

export interface StreamHooks {
  /** Fires once, whenever the socket ends for any reason (including `close()`). */
  onClose?: (info: StreamCloseInfo) => void
}

export interface StreamHandle {
  /** Resolves when the socket is open; rejects with an ApiError if it cannot be opened. */
  readonly ready: Promise<void>
  /** Send one chat message. Throws an ApiError unless the socket is open. */
  send(message: string): void
  /** Close the socket. Safe to call more than once. */
  close(): void
  readonly state: 'connecting' | 'open' | 'closed'
}

/**
 * Open the per-session event stream. The server answers each `{"message": ...}` frame with a
 * sequence of TurnEvents ending in a "done" event that carries the full TurnResult.
 */
export function openStream(
  sessionId: string,
  onEvent: (event: TurnEvent) => void,
  hooks: StreamHooks = {},
): StreamHandle {
  let state: StreamHandle['state'] = 'connecting'
  let closeRequested = false
  let rejectReady: (error: ApiError) => void = () => {}
  let resolveReady: () => void = () => {}
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve
    rejectReady = reject
  })
  // Callers may never await `ready` (e.g. after close()); do not surface that as unhandled.
  ready.catch(() => {})

  let socket: WebSocket
  try {
    socket = new WebSocket(streamUrl(sessionId))
  } catch (error) {
    state = 'closed'
    rejectReady(new ApiError(0, `Cannot open the event stream (${String(error)})`))
    queueMicrotask(() => hooks.onClose?.({ requested: false, code: 0, reason: 'construct failed' }))
    return {
      ready,
      send() {
        throw new ApiError(0, 'The event stream is not open')
      },
      close() {},
      get state() {
        return state
      },
    }
  }

  const openTimer = window.setTimeout(() => {
    if (state === 'connecting') {
      rejectReady(new ApiError(0, 'Timed out opening the event stream'))
      socket.close()
    }
  }, STREAM_OPEN_TIMEOUT_MS)

  socket.addEventListener('open', () => {
    window.clearTimeout(openTimer)
    state = 'open'
    resolveReady()
  })

  socket.addEventListener('message', (message: MessageEvent) => {
    const event = parseTurnEvent(message.data)
    if (event) onEvent(event)
  })

  socket.addEventListener('close', (event: CloseEvent) => {
    window.clearTimeout(openTimer)
    if (state === 'connecting') {
      rejectReady(new ApiError(0, 'The event stream closed before it opened'))
    }
    state = 'closed'
    hooks.onClose?.({ requested: closeRequested, code: event.code, reason: event.reason })
  })

  return {
    ready,
    send(message: string) {
      if (state !== 'open') throw new ApiError(0, 'The event stream is not open')
      socket.send(JSON.stringify({ message }))
    },
    close() {
      closeRequested = true
      window.clearTimeout(openTimer)
      if (state !== 'closed') socket.close()
    },
    get state() {
      return state
    },
  }
}
