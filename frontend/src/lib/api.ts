import type {
  ChatMessage,
  ClarifyPayload,
  EvalDataset,
  EvalRun,
  EvalRunSummary,
  ClientProfile,
  FxSnapshot,
  GuideChunk,
  RunTrace,
  TrendingPlace,
  TrendingTrace,
  TripContext,
  TripPlan,
} from "../design-system/types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export interface SessionState {
  session: { id: string; client_token: string; created_at: string };
  context: TripContext;
  plan: TripPlan | null;
  versions: { trip_id: string; version: number; change_summary: string; created_at: string; item_count: number; destination: string; days: number }[];
  messages: ChatMessage[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeader(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function createSession(clientToken: string): Promise<{ session: { id: string }; context: TripContext }> {
  return request("/api/journeys", { method: "POST", body: JSON.stringify({ client_token: clientToken }) });
}

export function getSession(sessionId: string): Promise<SessionState> {
  return request(`/api/journeys/${sessionId}`);
}

export function patchContext(sessionId: string, patch: Partial<TripContext>): Promise<{ context: TripContext }> {
  return request(`/api/journeys/${sessionId}/context`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function getPlan(sessionId: string, version?: number): Promise<{ plan: TripPlan }> {
  const query = version ? `?version=${version}` : "";
  return request(`/api/journeys/${sessionId}/plan${query}`);
}

export function postFeedback(
  sessionId: string,
  useful: boolean,
  stars: number | null,
  clientToken = localStorage.getItem("pathfinder.client") || "",
): Promise<{ feedback: { id: string }; preferences_added: string[] }> {
  return request(`/api/journeys/${sessionId}/feedback`, {
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
  const data = (await request(`/api/journeys/${sessionId}/legs${query}`)) as { legs?: MapLeg[] };
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

export function claimSession(sessionId: string): Promise<{ claimed: boolean }> {
  return request("/api/account/attach", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export function shareSession(sessionId: string): Promise<{ slug: string; url: string }> {
  return request(`/api/journeys/${sessionId}/share`, { method: "POST" });
}

export function clearMessages(sessionId: string): Promise<{ deleted: number }> {
  return request(`/api/journeys/${sessionId}/turns`, { method: "DELETE" });
}

export function discardSession(sessionId: string): Promise<{ deleted: Record<string, number> }> {
  return request(`/api/journeys/${sessionId}`, { method: "DELETE" });
}

export function getProfile(clientToken: string): Promise<ClientProfile> {
  return request(`/api/traveller?client_token=${encodeURIComponent(clientToken)}`);
}

export function saveProfilePreferences(clientToken: string, preferences: string[]): Promise<{ profile: ClientProfile }> {
  return request("/api/traveller", {
    method: "PATCH",
    body: JSON.stringify({ client_token: clientToken, preferences }),
  });
}

export function clearMemory(): Promise<{ cleared: boolean; saved_trips: number; preferences: number }> {
  return request("/api/recall/clear", { method: "POST" });
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
  const params = new URLSearchParams();
  if (query.trim()) params.set("q", query.trim());
  if (options.clientToken) params.set("client_token", options.clientToken);
  if (options.destination) params.set("destination", options.destination);
  const suffix = params.toString();
  return request(`/api/popular${suffix ? `?${suffix}` : ""}`);
}

export async function getSavedTrips(): Promise<{
  trips: { id: string; trip_id: string; destination: string; rating: number; stars: number | null; summary: string; created_at: string; days: number | null }[];
}> {
  return request("/api/wishlist");
}

export async function getEvalDatasets(): Promise<{ datasets: EvalDataset[] }> {
  return request("/api/eval/suites");
}

export async function getEvalRuns(): Promise<{ runs: EvalRunSummary[] }> {
  return request("/api/eval/trials");
}

export async function getEvalRun(runId: string): Promise<{ run: EvalRun }> {
  return request(`/api/eval/trials/${runId}`);
}

export async function runEval(datasetId: string, tag: string): Promise<{ run: EvalRun }> {
  return request("/api/eval/trials", {
    method: "POST",
    body: JSON.stringify({ dataset_id: datasetId, experiment_tag: tag, split: "all" }),
  });
}

export function evalExportUrl(runId: string): string {
  return `${BASE}/api/eval/trials/${runId}/export`;
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
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${BASE}/api/turn/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || "The chat stream could not be opened.");
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

export type { ClarifyPayload, EvalRun, RunTrace, TripPlan };
