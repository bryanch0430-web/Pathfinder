import type {
  ChatMessage,
  ClarifyPayload,
  EvalDataset,
  EvalRun,
  EvalRunSummary,
  ClientProfile,
  FxSnapshot,
  GuideChunk,
  PlanLocale,
  PlanTranslation,
  RunTrace,
  TrendingPlace,
  TrendingTrace,
  TripContext,
  TripPlan,
} from "../design-system/types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export interface SessionState {
  session: { id: string; created_at: string };
  context: TripContext;
  plan: TripPlan | null;
  versions: { trip_id: string; version: number; change_summary: string; created_at: string; item_count: number; destination: string; days: number }[];
  messages: ChatMessage[];
}

/**
 * A non-2xx API response; `status` lets a caller tell a refused identity (401) apart. `detail` is the
 * sentence the server wrote for the traveller (FastAPI's {"detail": "..."}); it is empty for any other
 * body, and `message` is then only the status line, for the console.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail = "",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The sentence from FastAPI's {"detail": "..."} body, or "" for any other body. A proxy's HTML error
 * page, a plain-text 500 or a list of field errors is not written for the traveller and is not shown.
 */
async function errorText(response: Response): Promise<string> {
  const body = await response.text();
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // Not JSON (or JSON without a detail): there is no sentence to show.
  }
  return "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeader(), ...clientTokenHeader(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await errorText(response);
    throw new ApiError(detail || `${response.status} ${response.statusText}`, response.status, detail);
  }
  return (await response.json()) as T;
}

export function createSession(clientToken: string): Promise<{ session: { id: string }; context: TripContext }> {
  return request("/api/journeys", { method: "POST", body: JSON.stringify({ client_token: clientToken }) });
}

export function getSession(sessionId: string): Promise<SessionState> {
  return request(`/api/journeys/${sessionId}`);
}

/** Body of PATCH /state (backend `ContextPatch`): every field optional; a null leaves the value unchanged. */
export type ContextPatch = Partial<Omit<TripContext, "session_id">>;

export function patchContext(sessionId: string, patch: ContextPatch): Promise<{ context: TripContext }> {
  return request(`/api/journeys/${sessionId}/state`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function getPlan(sessionId: string, version?: number): Promise<{ plan: TripPlan }> {
  const query = version ? `?version=${version}` : "";
  return request(`/api/journeys/${sessionId}/itinerary${query}`);
}

/** Display text for one plan version in `locale`; numbers, times and ids stay on the English plan. */
export function getTranslation(sessionId: string, version: number, locale: PlanLocale): Promise<PlanTranslation> {
  const params = new URLSearchParams({ version: String(version), locale });
  return request(`/api/journeys/${sessionId}/translation?${params.toString()}`);
}

export function postFeedback(
  sessionId: string,
  useful: boolean,
  stars: number | null,
  clientToken = localStorage.getItem("pathfinder.client") || "",
): Promise<{ feedback: { id: string }; preferences_added: string[] }> {
  return request(`/api/journeys/${sessionId}/rating`, {
    method: "POST",
    body: JSON.stringify({ useful, stars, client_token: clientToken }),
  });
}

export async function getConfig(): Promise<Record<string, unknown>> {
  return request("/api/runtime");
}

export interface MapLeg {
  from_id: string;
  to_id: string;
  mode: string;
  minutes: number | null;
  km: number | null;
  /** [lat, lng] pairs of the real route geometry. */
  path: [number, number][];
  source: string;
}

/** Real route geometry per leg, including the legs to restaurants. */
export async function getLegs(sessionId: string, day?: string): Promise<MapLeg[]> {
  const query = day ? `?day=${encodeURIComponent(day)}` : "";
  const data = (await request(`/api/journeys/${sessionId}/segments${query}`)) as { legs?: MapLeg[] };
  return data.legs ?? [];
}

export interface MapKeys {
  google: { key: string; enabled: boolean };
}

/** Browser map keys. Google Maps is the only base map. */
export async function getMapKeys(): Promise<MapKeys> {
  const config = await getConfig();
  const maps = (config.maps ?? {}) as Partial<MapKeys>;
  return {
    google: { key: maps.google?.key ?? "", enabled: Boolean(maps.google?.enabled) },
  };
}

export interface PlaceSuggestion {
  label: string;
  place_id: string;
}

/** Places API (New) autocomplete for the destination field. */
export function suggestPlaces(query: string): Promise<{ items: PlaceSuggestion[] }> {
  return request(`/api/places/suggest?q=${encodeURIComponent(query)}`);
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface AuthResponse {
  token: string;
  token_type: string;
  profile_token: string;
  user: AuthUser;
}

export function authHeader(): Record<string, string> {
  const token = localStorage.getItem("pathfinder.auth");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** The operator's admin token, kept in this browser under the key the Settings page reads; empty when none. */
export function adminHeader(): Record<string, string> {
  const token = localStorage.getItem("pathfinder.admin");
  return token ? { "X-Admin-Token": token } : {};
}

/** A guest trip answers only to the token its browser created it with, so every call presents it. */
export function clientTokenHeader(): Record<string, string> {
  return { "X-Client-Token": localStorage.getItem("pathfinder.client") ?? "" };
}

export function register(payload: {
  email: string;
  password: string;
  display_name?: string;
  client_token?: string;
  session_id?: string | null;
}): Promise<AuthResponse> {
  return request("/api/account/signup", { method: "POST", body: JSON.stringify(payload) });
}

export function login(payload: {
  email: string;
  password: string;
  client_token?: string;
  session_id?: string | null;
}): Promise<AuthResponse> {
  return request("/api/account/signin", { method: "POST", body: JSON.stringify(payload) });
}

export function me(): Promise<{ user: AuthUser; profile_token: string }> {
  return request("/api/account/whoami");
}

/** Moves a guest session to the signed-in account; `clientToken` is the guest token it was created with. */
export function claimSession(sessionId: string, clientToken: string): Promise<{ claimed: boolean }> {
  return request("/api/account/attach", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, client_token: clientToken }),
  });
}

/** A read-only share link; `url` is a path on this origin, `expires_at` an ISO 8601 time. */
export interface ShareLink {
  slug: string;
  url: string;
  expires_at: string;
}

export function shareSession(sessionId: string): Promise<ShareLink> {
  return request(`/api/journeys/${sessionId}/share`, { method: "POST" });
}

/** Turns off the trip's share link; `revoked` is how many links were live. */
export function revokeShare(sessionId: string): Promise<{ revoked: number }> {
  return request(`/api/journeys/${sessionId}/share`, { method: "DELETE" });
}

export function clearMessages(sessionId: string): Promise<{ deleted: number }> {
  return request(`/api/journeys/${sessionId}/turns`, { method: "DELETE" });
}

export function discardSession(sessionId: string): Promise<{ deleted: Record<string, number> }> {
  return request(`/api/journeys/${sessionId}`, { method: "DELETE" });
}

/** The guest token travels in the `X-Client-Token` header, never in a URL: servers and proxies log URLs. */
export function getProfile(clientToken: string): Promise<ClientProfile> {
  return request("/api/traveller", { headers: { "X-Client-Token": clientToken } });
}

export function saveProfilePreferences(clientToken: string, preferences: string[]): Promise<{ profile: ClientProfile }> {
  return request("/api/traveller", {
    method: "PATCH",
    body: JSON.stringify({ client_token: clientToken, preferences }),
  });
}

/** Clears only the caller's saved trips and preferences (guest token, or the signed-in bearer). */
export function clearMemory(clientToken: string): Promise<{ cleared: boolean; saved_trips: number; preferences: number }> {
  return request("/api/recall/clear", { method: "POST", body: JSON.stringify({ client_token: clientToken }) });
}

export function getGuides(destination: string, q = ""): Promise<{ items: GuideChunk[]; note: string; retrieval?: { mode: string; reranked: boolean } }> {
  const params = new URLSearchParams({ destination, limit: "3" });
  if (q) params.set("q", q);
  return request(`/api/handbook?${params.toString()}`);
}

export function getFx(base = "HKD"): Promise<FxSnapshot> {
  return request(`/api/wallet?base=${encodeURIComponent(base)}`);
}
export async function getTrending(
  query = "",
  options: { clientToken?: string; destination?: string } = {},
): Promise<{
  items: TrendingPlace[];
  trace: TrendingTrace;
}> {
  const load = (clientToken?: string) => {
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (options.destination) params.set("destination", options.destination);
    const suffix = params.toString();
    // The header always names the token this call personalises with, or none (an empty header).
    return request<{ items: TrendingPlace[]; trace: TrendingTrace }>(`/api/popular${suffix ? `?${suffix}` : ""}`, {
      headers: { "X-Client-Token": clientToken ?? "" },
    });
  };
  try {
    return await load(options.clientToken);
  } catch (error) {
    // The list is public: a `user:` token whose sign-in expired is refused with
    // 401, so load it again without personal matching.
    if (error instanceof ApiError && error.status === 401 && options.clientToken) return load();
    throw error;
  }
}

/** The caller's own saved trips (guest token, or the signed-in bearer). */
export async function getSavedTrips(clientToken: string): Promise<{
  trips: { id: string; trip_id: string; destination: string; rating: number; stars: number | null; summary: string; created_at: string; days: number | null }[];
}> {
  return request("/api/wishlist", { headers: { "X-Client-Token": clientToken } });
}

// Every /api/eval route needs the admin token (X-Admin-Token).
export async function getEvalDatasets(): Promise<{ datasets: EvalDataset[] }> {
  return request("/api/eval/suites", { headers: adminHeader() });
}

export async function getEvalRuns(): Promise<{ runs: EvalRunSummary[] }> {
  return request("/api/eval/trials", { headers: adminHeader() });
}

export async function getEvalRun(runId: string): Promise<{ run: EvalRun }> {
  return request(`/api/eval/trials/${runId}`, { headers: adminHeader() });
}

export async function runEval(datasetId: string, tag: string): Promise<{ run: EvalRun }> {
  return request("/api/eval/trials", {
    method: "POST",
    headers: adminHeader(),
    body: JSON.stringify({ dataset_id: datasetId, experiment_tag: tag, split: "all" }),
  });
}

/** The run report as text. A plain link cannot send the admin header, so the page fetches it. */
export async function getEvalExport(runId: string): Promise<string> {
  const response = await fetch(`${BASE}/api/eval/trials/${runId}/export`, { headers: adminHeader() });
  if (!response.ok) {
    const detail = await errorText(response);
    throw new ApiError(detail || `${response.status} ${response.statusText}`, response.status, detail);
  }
  return response.text();
}

export async function getInjections(adminToken: string): Promise<{ events: { category: string; severity: string; count: number }[] }> {
  return request("/api/admin/threats", { headers: { "X-Admin-Token": adminToken } });
}

export interface StreamHandlers {
  onEvent: (event: string, data: Record<string, unknown>) => void;
  onError?: (error: Error) => void;
}

export async function streamChat(
  payload: {
    session_id: string;
    message: string;
    locale: string;
    scope_item_id?: string | null;
    scope_label?: string | null;
    developer?: boolean;
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${BASE}/api/turn/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader(), ...clientTokenHeader() },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    const detail = await errorText(response);
    throw new ApiError(detail || "The chat stream could not be opened.", response.status, detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (!dataLines.length) continue;
      try {
        handlers.onEvent(event, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
      } catch (error) {
        handlers.onError?.(error instanceof Error ? error : new Error(String(error)));
      }
    }
  }
}

export type { ClarifyPayload, EvalRun, PlanLocale, PlanTranslation, RunTrace, TripPlan };
