import type {
  ChatAttachment,
  ChatMessage,
  ClarifyPayload,
  EvalDataset,
  EvalPreview,
  EvalRun,
  EvalRunSummary,
  ClientProfile,
  FxSnapshot,
  GuideChunk,
  PlanLocale,
  PlanTranslation,
  RoomType,
  RunTrace,
  TrendingPlace,
  TrendingTrace,
  TravellerProfile,
  TripContext,
  TripPlan,
} from "../design-system/types";

import { readStoredValue } from "./storage";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export interface SessionState {
  session: { id: string; created_at: string };
  context: TripContext;
  plan: TripPlan | null;
  versions: { trip_id: string; version: number; change_summary: string; created_at: string; item_count: number; destination: string; days: number }[];
  messages: ChatMessage[];
  /** A person on the desk is answering this chat, so the planner waits. */
  held?: boolean;
  /** The signed-in name of that person, empty when the chat is not held. */
  agent_name?: string;
  /** A turn is writing this plan, so direct edit must wait. */
  planner_busy?: boolean;
  /** What that turn last said it was doing. */
  planner_note?: string;
  /** The latest rating saved for this trip, so a reload or tab switch remembers it. */
  feedback?: { useful: boolean; stars: number | null; created_at: string } | null;
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

export function createSession(clientToken: string, signal?: AbortSignal): Promise<{ session: { id: string }; context: TripContext }> {
  return request("/api/journeys", { method: "POST", body: JSON.stringify({ client_token: clientToken }), signal });
}

/** Write the direct-edit grid onto the latest plan. `day` is 1-based; `planVersion` is the version the grid showed. */
export function applyLayout(
  sessionId: string,
  items: { id: string; day: number; start: string; end: string; name?: string }[],
  planVersion?: number,
  drops: string[] = [],
): Promise<{ plan: TripPlan }> {
  return request(`/api/journeys/${sessionId}/layout`, {
    method: "POST",
    body: JSON.stringify({ items, drops, plan_version: planVersion }),
  });
}

export function decideProposal(
  sessionId: string,
  messageId: string,
  changeId: string,
  accept: boolean,
): Promise<{ plan: TripPlan; message: ChatMessage }> {
  return request(`/api/journeys/${sessionId}/proposals/${messageId}`, {
    method: "POST",
    body: JSON.stringify({ change_id: changeId, accept }),
  });
}

/** Open a saved plan file as a new trip. `messages` restores the conversation when the file has one. */
export function importPlan(
  clientToken: string,
  plan: unknown,
  signal?: AbortSignal,
  messages: unknown[] = [],
): Promise<SessionState> {
  return request("/api/journeys/import", {
    method: "POST",
    body: JSON.stringify({ client_token: clientToken, plan, messages }),
    signal,
  });
}

export function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionState> {
  return request(`/api/journeys/${sessionId}`, { signal });
}

/** Whether a person on the desk has taken this chat. Does not reload the plan. */
export function getHeld(sessionId: string): Promise<{ held: boolean; agent_name?: string; planner_busy?: boolean; planner_note?: string }> {
  return request(`/api/journeys/${encodeURIComponent(sessionId)}/held`);
}

/** The saved versions of a trip: a light call to find out whether the trip is still there (404 when it is not). */
export function getVersions(sessionId: string, signal?: AbortSignal): Promise<{ versions: SessionState["versions"] }> {
  return request(`/api/journeys/${sessionId}/revisions`, { signal });
}

/** Body of PATCH /state (backend `ContextPatch`): every field optional; a null leaves the value unchanged. */
export type ContextPatch = Partial<Omit<TripContext, "session_id" | "profile">> & Partial<TravellerProfile>;

export function patchContext(sessionId: string, patch: ContextPatch, signal?: AbortSignal): Promise<{ context: TripContext }> {
  return request(`/api/journeys/${sessionId}/state`, { method: "PATCH", body: JSON.stringify(patch), signal });
}

export function getPlan(sessionId: string, version?: number): Promise<{ plan: TripPlan }> {
  const query = version ? `?version=${version}` : "";
  return request(`/api/journeys/${sessionId}/itinerary${query}`);
}

/** Copy a saved version forward so it becomes the plan the next message edits. */
export function restorePlan(sessionId: string, version: number): Promise<{ plan: TripPlan }> {
  return request(`/api/journeys/${sessionId}/itinerary/restore`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

/** Display text for one plan version in `locale`; numbers, times and ids stay on the English plan. */
export function getTranslation(sessionId: string, version: number, locale: PlanLocale): Promise<PlanTranslation> {
  const params = new URLSearchParams({ version: String(version), locale });
  return request(`/api/journeys/${sessionId}/translation?${params.toString()}`);
}

export function postFinalise(sessionId: string): Promise<{ context: TripContext }> {
  return request(`/api/journeys/${sessionId}/finalise`, { method: "POST", body: "{}" });
}

export function postFeedback(
  sessionId: string,
  useful: boolean,
  stars: number | null,
  clientToken = readStoredValue("client") || "",
): Promise<{ feedback: { id: string }; preferences_added: string[] }> {
  return request(`/api/journeys/${sessionId}/rating`, {
    method: "POST",
    body: JSON.stringify({ useful, stars, client_token: clientToken }),
  });
}

export async function getConfig(): Promise<Record<string, unknown>> {
  return request("/api/runtime");
}

export type SerpKeyStatus = "active" | "ready" | "exhausted" | "paused";

export interface SerpKey {
  id: string;
  label: string;
  key_tail: string;
  status: SerpKeyStatus;
  position: number;
}

export function getSerpKeys(): Promise<{ keys: SerpKey[] }> {
  return request("/api/admin/serpapi-keys", { headers: deskHeader() });
}

export function addSerpKey(body: { api_key: string; label: string; status: SerpKeyStatus }): Promise<{ keys: SerpKey[] }> {
  return request("/api/admin/serpapi-keys", { method: "POST", headers: deskHeader(), body: JSON.stringify(body) });
}

export function setSerpKeyStatus(id: string, status: SerpKeyStatus): Promise<{ keys: SerpKey[] }> {
  return request(`/api/admin/serpapi-keys/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: deskHeader(),
    body: JSON.stringify({ status }),
  });
}

export function saveRuntime(body: {
  data_mode?: "live" | "corpus";
  purpose_models?: Record<string, string>;
  purpose_temperatures?: Record<string, number>;
  purpose_reasoning?: Record<string, string>;
  purpose_max_tokens?: Record<string, number>;
  orchestrator?: { mode: "analyst" | "jev"; jev?: { model: string; url: string; api_key?: string } };
  thresholds?: Record<string, number>;
  freshness_hours?: Record<string, number>;
  gate?: "jev" | "plain" | "reason";
  plain_model?: string;
  reason_model?: string;
  writer_model?: string;
  jev?: { model: string; url: string; api_key?: string; clear_key?: boolean };
  fast?: { model: string; url: string; api_key?: string; clear_key?: boolean };
  poe?: { api_key?: string; clear_key?: boolean };
  plain?: {
    provider: string;
    model: string;
    base_url: string;
    api_key?: string;
    clear_key?: boolean;
    temperature: number;
    max_tokens: number;
    reasoning: string;
  };
  reason?: {
    provider: string;
    model: string;
    base_url: string;
    api_key?: string;
    clear_key?: boolean;
    temperature: number;
    max_tokens: number;
    reasoning: string;
  };
  reviewer?: {
    provider: string;
    model: string;
    base_url: string;
    api_key?: string;
    clear_key?: boolean;
    temperature: number;
    max_tokens: number;
    reasoning: string;
    rounds: number;
  };
  writer_call?: {
    temperature: number;
    max_tokens: number;
    reasoning: string;
  };
  redaction?: { hkid: boolean; hk_phone: boolean; email: boolean; card: boolean; hkid_pattern?: string; hk_phone_pattern?: string; email_pattern?: string; card_pattern?: string; patterns?: { hkid: string; hk_phone: string; email: string; card: string } };
}): Promise<Record<string, unknown>> {
  return request("/api/admin/runtime", { method: "PUT", headers: deskHeader(), body: JSON.stringify(body) });
}

export function previewRedaction(body: {
  text: string;
  hkid: boolean;
  hk_phone: boolean;
  email: boolean;
  card: boolean;
  hkid_pattern?: string;
  hk_phone_pattern?: string;
  email_pattern?: string;
  card_pattern?: string;
}): Promise<{ person: string; machine: string }> {
  return request("/api/admin/redaction/preview", { method: "POST", headers: deskHeader(), body: JSON.stringify(body) });
}

export function resetRuntime(): Promise<Record<string, unknown>> {
  return request("/api/admin/runtime", { method: "DELETE", headers: deskHeader() });
}

/** The unauthenticated status: `local_admin` says whether this caller is in local admin mode. */
export function getReady(): Promise<Record<string, unknown>> {
  return request("/api/ready");
}

export interface MapStep {
  mode: string;
  minutes: number | null;
  path: [number, number][];
  instruction: string;
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
  /** Per-step detail; a WALK route can contain a ferry step. */
  steps?: MapStep[];
}

export interface MapAnchor {
  id: string;
  name: string;
  lat: number;
  lng: number;
}

/** Real route geometry per leg, including the legs to restaurants. */
export async function getLegs(sessionId: string, day?: string): Promise<{ legs: MapLeg[]; anchors: MapAnchor[] }> {
  const query = day ? `?day=${encodeURIComponent(day)}` : "";
  const data = (await request(`/api/journeys/${sessionId}/segments${query}`)) as { legs?: MapLeg[]; anchors?: MapAnchor[] };
  return { legs: data.legs ?? [], anchors: data.anchors ?? [] };
}

/** One way to travel a hop. Picking it updates the map line and the later times. */
export interface LegOption {
  mode: "walk" | "cycle" | "transit" | "drive";
  minutes: number | null;
  km: number | null;
  source: string;
  routed: boolean;
  ferry?: boolean;
  /** [lat, lng] pairs of this mode's route. */
  path?: [number, number][];
  steps?: MapStep[];
}

export interface LegOptions {
  from_id: string;
  to_id: string;
  from_name: string;
  to_name: string;
  options: LegOption[];
}

export function getLegOptions(sessionId: string, fromId: string, toId: string): Promise<LegOptions> {
  const query = `from_id=${encodeURIComponent(fromId)}&to_id=${encodeURIComponent(toId)}`;
  return request(`/api/journeys/${sessionId}/leg-options?${query}`);
}

export type ChoiceKind = "item" | "hotel" | "flight" | "transport";

/** Swap in another option shown on the plan (a stop's alternative, a hotel, a flight); no model call. */
export function chooseOption(
  sessionId: string,
  body: { kind: ChoiceKind; target_id?: string | null; option_id: string; plan_version?: number; lock?: boolean },
): Promise<{ plan: TripPlan }> {
  return request(`/api/journeys/${sessionId}/plan/choose`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Lock or unlock a chosen stay (`hotel`) or leg (`transport`) in place: no new version, no model call. */
export function setChoiceLock(
  sessionId: string,
  body: { kind: "hotel" | "transport"; target_id: string; locked: boolean; plan_version?: number },
): Promise<{ plan: TripPlan }> {
  return request(`/api/journeys/${sessionId}/lock`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Mark a locked stay (`hotel`) or leg (`transport`) as booked or not, in place: no new version, no model call. */
export function setChoiceBooked(
  sessionId: string,
  body: { kind: "hotel" | "transport"; target_id: string; booked: boolean; plan_version?: number },
): Promise<{ plan: TripPlan }> {
  return request(`/api/journeys/${sessionId}/booked`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** The rooms the hotel source lists for a stay; fetched once, then kept on the plan. Empty when it lists none. */
export function getHotelRooms(sessionId: string, hotelId: string): Promise<{ rooms: RoomType[] }> {
  return request(`/api/journeys/${sessionId}/plan/hotels/${encodeURIComponent(hotelId)}/rooms`);
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
  const token = readStoredValue("auth");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** The operator's admin token, kept in this browser under the key the Settings page reads; empty when none. */
export function adminHeader(): Record<string, string> {
  const token = readStoredValue("admin");
  return token ? { "X-Admin-Token": token } : {};
}

/** Desk calls also send the signed-in account, so a reply can carry that person's name. */
function deskHeader(): Record<string, string> {
  return { ...authHeader(), ...adminHeader() };
}

/* ------------------------------------------------------------ LLM keys -- */

/** One provider the traveller can bring a key for (GET /api/llm/providers). */
export interface LlmProvider {
  id: string;
  label: string;
  default_model: string;
  key_prefix: string;
  supports_search: boolean;
  /** True for the generic OpenAI-compatible entry: the form must ask for the endpoint to call. */
  needs_base_url?: boolean;
}

/** A saved key as the server shows it: only its masked tail, never the key. */
export interface LlmKey {
  id: string;
  provider: string;
  label: string;
  model: string;
  /** The endpoint a generic OpenAI-compatible key calls; empty for a fixed vendor. */
  base_url?: string;
  key_tail: string;
  active: boolean;
  use_for_search: boolean;
  last_test_ok: boolean | null;
  last_tested_at: string | null;
  last_error: string;
  unreadable: boolean;
}

/** Which model answers a turn and whose key pays for it. */
export interface LlmStatus {
  source: "byok" | "server" | "env" | "local" | "none";
  provider: string;
  model: string;
  /** Set when the chosen key cannot be used: "unreadable" or "unknown_provider". Empty or absent when it can. */
  problem?: string;
  local: { configured: boolean; reachable: boolean; model: string };
}

export type KeyTestKind = "ok" | "auth" | "model" | "rate_limit" | "network" | "credit" | "other";

export interface KeyTestResult {
  ok: boolean;
  kind: KeyTestKind;
  message: string;
  latency_ms: number;
  models: string[];
}

/** The body of a test or a save. The key is sent in the body only, never in a URL. */
export interface KeyBody {
  provider: string;
  api_key: string;
  model: string;
  /** The endpoint for the generic OpenAI-compatible provider; omitted for a fixed vendor. */
  base_url?: string;
  use_for_search?: boolean;
}

/** `server` addresses the operator's own row: it adds `?scope=server` and the admin token. */
export type KeyScope = "server" | undefined;

function keyRequest<T>(path: string, scope: KeyScope, init: RequestInit = {}): Promise<T> {
  if (scope !== "server") return request<T>(path, init);
  const url = `${path}${path.includes("?") ? "&" : "?"}scope=server`;
  return request<T>(url, { ...init, headers: { ...adminHeader(), ...(init.headers ?? {}) } });
}

export function getLlmProviders(): Promise<{ providers: LlmProvider[] }> {
  return request("/api/llm/providers");
}

export function getLlmKeys(scope?: KeyScope): Promise<{ keys: LlmKey[]; status: LlmStatus }> {
  return keyRequest("/api/llm/keys", scope);
}

export function testLlmKey(body: KeyBody, scope?: KeyScope): Promise<KeyTestResult> {
  const { provider, api_key, model, base_url } = body;
  const sent = base_url ? { provider, api_key, model, base_url } : { provider, api_key, model };
  return keyRequest("/api/llm/keys/test", scope, { method: "POST", body: JSON.stringify(sent) });
}

/** Saves a key. A `warning` means it was kept although the test could not finish (rate limit, network, credit). */
export function saveLlmKey(body: KeyBody, scope?: KeyScope): Promise<{ key: LlmKey; warning?: string }> {
  return keyRequest("/api/llm/keys", scope, { method: "POST", body: JSON.stringify(body) });
}

export function retestLlmKey(id: string, scope?: KeyScope): Promise<{ key: LlmKey; result: KeyTestResult }> {
  return keyRequest(`/api/llm/keys/${encodeURIComponent(id)}/test`, scope, { method: "POST" });
}

export function patchLlmKey(
  id: string,
  body: { model?: string; base_url?: string; use_for_search?: boolean },
  scope?: KeyScope,
): Promise<{ key: LlmKey }> {
  return keyRequest(`/api/llm/keys/${encodeURIComponent(id)}`, scope, { method: "PATCH", body: JSON.stringify(body) });
}

/** Chooses the key that answers, or null for the default (no key of one's own). */
export function chooseLlmKey(keyId: string | null, scope?: KeyScope): Promise<{ status: LlmStatus }> {
  return keyRequest("/api/llm/choice", scope, { method: "PUT", body: JSON.stringify({ key_id: keyId }) });
}

export function deleteLlmKey(id: string, scope?: KeyScope): Promise<{ deleted: boolean }> {
  return keyRequest(`/api/llm/keys/${encodeURIComponent(id)}`, scope, { method: "DELETE" });
}

/** A guest trip answers only to the token its browser created it with, so every call presents it. */
export function clientTokenHeader(): Record<string, string> {
  return { "X-Client-Token": readStoredValue("client") ?? "" };
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

export function me(signal?: AbortSignal): Promise<{ user: AuthUser; profile_token: string }> {
  return request("/api/account/whoami", { signal });
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

/** Remove one saved trip. The original planning session is not touched. */
export function deleteSavedTrip(clientToken: string, id: string): Promise<{ deleted: boolean }> {
  return request(`/api/wishlist/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { "X-Client-Token": clientToken },
  });
}

/** Open a saved trip as a new planning session. The saved copy stays as it was. */
export function openSavedTrip(clientToken: string, id: string): Promise<SessionState> {
  return request(`/api/wishlist/${encodeURIComponent(id)}/open`, {
    method: "POST",
    headers: { "X-Client-Token": clientToken },
  });
}

/** The caller's own saved trips (guest token, or the signed-in bearer). */
export async function getSavedTrip(clientToken: string, id: string): Promise<{
  id: string;
  destination: string;
  summary: string;
  stars: number | null;
  days: { date: string; title: string; items: string[] }[];
}> {
  return request(`/api/wishlist/${encodeURIComponent(id)}?client_token=${encodeURIComponent(clientToken)}`, {
    headers: { "X-Client-Token": clientToken },
  });
}

export async function getSavedTrips(clientToken: string): Promise<{
  trips: { id: string; trip_id: string; destination: string; rating: number; stars: number | null; useful?: boolean | null; summary: string; created_at: string; days: number | null }[];
}> {
  return request("/api/wishlist", { headers: { "X-Client-Token": clientToken } });
}

// Every /api/eval route needs the admin token (X-Admin-Token).
export async function getEvalSuiteCases(datasetId: string): Promise<{ cases: EvalPreview[] }> {
  return request(`/api/eval/suites/${encodeURIComponent(datasetId)}/cases`, { headers: adminHeader() });
}

export async function getEvalReference(): Promise<import("../design-system/types").EvalReference> {
  return request("/api/eval/reference", { headers: adminHeader() });
}

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

/** The stored plan as an .ics calendar file. A plain link cannot send the identity headers, so the page fetches it. */
export async function getPlanCalendar(sessionId: string): Promise<Blob> {
  const response = await fetch(`${BASE}/api/journeys/${encodeURIComponent(sessionId)}/calendar.ics`, {
    headers: { ...authHeader(), ...clientTokenHeader() },
  });
  if (!response.ok) {
    const detail = await errorText(response);
    throw new ApiError(detail || `${response.status} ${response.statusText}`, response.status, detail);
  }
  return response.blob();
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

export interface ReviewCase {
  source: "evaluation" | "session" | "operator";
  session_state: "" | "live" | "completed";
  run_id: string;
  dataset: string;
  case_id: string;
  kind: "misclassified" | "failed" | "unchanged" | "not_useful" | "added";
  failure_class: string;
  input: string;
  earlier?: string;
  output_summary: string;
  judge_reason: string;
  check_status?: "" | "pass" | "fail" | "chat";
  check_summary?: string;
}

export interface SkillSuggestion {
  id: string;
  status: "draft" | "accepted" | "dismissed" | "restored";
  skill: string;
  summary: string;
  rationale: string;
  body: string;
  case_ids: string[];
  created_at: string;
  decided_at: string | null;
  kind?: "skill" | "setting";
  setting_value?: string;
}

export function getReviewCases(): Promise<{ cases: ReviewCase[]; suggestions: SkillSuggestion[] }> {
  return request("/api/admin/cases", { headers: adminHeader() });
}

export function scanReviewCases(): Promise<{ suggestions: SkillSuggestion[] }> {
  return request("/api/admin/cases/scan", { method: "POST", headers: adminHeader() });
}

export function checkReviewCases(): Promise<{ passed: number; exam: number; cases: ReviewCase[] }> {
  return request("/api/admin/cases/check", { method: "POST", headers: adminHeader() });
}

export function acceptSuggestion(id: string, body: string): Promise<{ suggestion: SkillSuggestion }> {
  return request(`/api/admin/cases/suggestions/${encodeURIComponent(id)}/accept`, {
    method: "POST",
    headers: adminHeader(),
    body: JSON.stringify({ body }),
  });
}

export function dismissSuggestion(id: string): Promise<{ suggestion: SkillSuggestion }> {
  return request(`/api/admin/cases/suggestions/${encodeURIComponent(id)}/dismiss`, {
    method: "POST",
    headers: adminHeader(),
  });
}

export function restoreSuggestion(id: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/cases/suggestions/${encodeURIComponent(id)}/restore`, {
    method: "POST",
    headers: adminHeader(),
  });
}

export interface CaptureTurn {
  id: string;
  session_id: string;
  session_state: "" | "live" | "completed";
  input: string;
  reply: string;
}

export function getCaptureTurns(): Promise<{ turns: CaptureTurn[] }> {
  return request("/api/admin/cases/turns", { headers: adminHeader() });
}

export function addOperatorCase(body: {
  message: string;
  expected: string;
  note: string;
  session_id: string;
  session_state: string;
}): Promise<{ cases: ReviewCase[] }> {
  return request("/api/admin/cases", { method: "POST", headers: adminHeader(), body: JSON.stringify(body) });
}

/** Without `adminToken` it sends the stored one when there is one (in local admin mode there may be none). */
export async function getInjections(adminToken = ""): Promise<{ events: { category: string; severity: string; count: number }[] }> {
  return request("/api/admin/threats", { headers: adminToken ? { "X-Admin-Token": adminToken } : adminHeader() });
}

export interface DeskContact {
  destination: string;
  origin: string;
  start: string;
  end: string;
  party: number | null;
  budget: number | null;
  currency: string;
  signed_in?: boolean;
  name?: string;
  email?: string;
  preferences?: string[];
}

export interface DeskStop {
  id: string;
  name: string;
  type: string;
  start: string;
  end: string;
  address?: string;
}

export interface DeskPlan {
  version: number;
  days: { date: string; title: string; items: DeskStop[] }[];
}

export interface DeskChat extends DeskContact {
  session_id: string;
  created_at?: string;
  updated_at: string;
  /** live: the page is open, or the trip changed within 24 hours. completed: neither. */
  state: "live" | "completed";
  on_page: boolean;
  /** A finalised plan. It stays in session history and cannot be taken over. */
  finished?: boolean;
  held: boolean;
  /** Set once a person has picked the chat up. Empty while it is only waiting in the queue. */
  agent_name?: string;
  stuck?: boolean;
  frustration: number;
  /** session: average of at least three lines. line: only the latest line. none: nothing scored. */
  frustration_kind?: "session" | "line" | "none";
  band: "calm" | "uneasy" | "hot";
  sentiment_score?: number | null;
  sentiment_label?: string;
  sentiment_notes?: string[];
  preview: string;
  last_role: string;
}

export interface DeskMessage {
  id: string;
  role: string;
  content: string;
  at: string;
  frustration: number;
  sentiment_label?: string;
  operator?: boolean;
  live_agent: boolean;
}

export function getDesk(): Promise<{ chats: DeskChat[] }> {
  return request("/api/admin/desk", { headers: deskHeader() });
}

export function getDeskChat(sessionId: string): Promise<{ session_id: string; held: boolean; note: string; contact: DeskContact; messages: DeskMessage[] }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}`, { headers: deskHeader() });
}

export function holdDeskChat(sessionId: string): Promise<{ held: boolean; agent_name?: string }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/hold`, { method: "POST", headers: deskHeader() });
}

export function releaseDeskChat(sessionId: string): Promise<{ held: boolean; agent_name?: string }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/release`, { method: "POST", headers: deskHeader() });
}

export function saveDeskNote(sessionId: string, note: string): Promise<{ note: string }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/note`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ note }),
  });
}

export interface ScoredRow {
  text: string;
  score: number;
}

export interface MonitorPart {
  enabled: boolean;
  engine: string;
  words: ScoredRow[];
  examples: ScoredRow[];
  score: number | null;
  message: string;
  after: number;
}

export interface EscalationPolicy {
  live_agent?: boolean;
  phrases_on?: boolean;
  enabled: boolean;
  message: string;
  frustration_at: number | null;
  match_mode?: "examples" | "keywords";
  use_jev?: boolean;
  stuck_on?: boolean;
  stuck_after?: number;
  human_phrases?: string[];
  words?: string[];
  key_source?: "saved" | "gate" | "none";
  key_set?: boolean;
  key_saved?: boolean;
  key_tail?: string;
  examples: { id: string; utterance: string }[];
  parts?: { hand: MonitorPart; sentiment: MonitorPart; stuck: MonitorPart };
}

export interface PromptRow {
  name: string;
  description: string;
  default: string;
  override: string;
  file?: string;
}

export function getEscalation(): Promise<EscalationPolicy> {
  return request("/api/admin/escalation", { headers: deskHeader() });
}

export function saveEscalation(body: {
  live_agent?: boolean;
  phrases_on?: boolean;
  enabled: boolean;
  message: string;
  frustration_at: number | null;
  stuck_on: boolean;
  stuck_after: number;
  human_phrases?: string[];
  /** v4 fields, kept optional so an old client does not break; v5 ignores them. */
  examples?: string[];
  match_mode?: "examples" | "keywords";
  use_jev?: boolean;
  words?: string[];
  api_key?: string;
  clear_key?: boolean;
  monitors?: { hand: MonitorPart; sentiment: MonitorPart; stuck: MonitorPart };
}): Promise<EscalationPolicy> {
  return request("/api/admin/escalation", { method: "PUT", headers: deskHeader(), body: JSON.stringify(body) });
}

export function getPrompts(): Promise<{ prompts: PromptRow[] }> {
  return request("/api/admin/prompts", { headers: deskHeader() });
}

export function savePrompt(name: string, body: string): Promise<{ name: string }> {
  return request(`/api/admin/prompts/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: deskHeader(),
    body: JSON.stringify({ body }),
  });
}

export function resetPrompt(name: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/prompts/${encodeURIComponent(name)}`, { method: "DELETE", headers: deskHeader() });
}

export function saveDeskBrief(
  sessionId: string,
  body: {
    destination: string;
    origin: string;
    start_date: string;
    end_date: string;
    party_size: number;
    budget: number;
    currency: string;
    preferences: string[];
  },
): Promise<{ built: boolean; plan: DeskPlan | null }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/brief`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify(body),
  });
}

export function runDeskWorkflow(sessionId: string, message: string): Promise<{ reply: string; intent: string }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/run`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ message }),
  });
}

export function getDeskPlan(sessionId: string): Promise<{ plan: DeskPlan | null }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/plan`, { headers: deskHeader() });
}

export function getDeskRevisions(sessionId: string): Promise<{ versions: { version: number; change_summary?: string }[] }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/revisions`, { headers: deskHeader() });
}

export function undoDeskPlan(sessionId: string, version: number): Promise<{ plan: TripPlan }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/undo`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ version }),
  });
}

export function getDeskItinerary(sessionId: string): Promise<{ plan: TripPlan | null; planner_busy?: boolean; planner_note?: string }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/itinerary`, { headers: deskHeader() });
}

export function applyDeskLayout(
  sessionId: string,
  items: { id: string; day: number; start: string; end: string; name?: string }[],
  planVersion?: number,
): Promise<{ plan: TripPlan }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/layout`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ items, plan_version: planVersion }),
  });
}

export function editDeskPlan(
  sessionId: string,
  instruction: string,
  itemId = "",
): Promise<{ reply: string; plan: DeskPlan | null }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/edit`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ instruction, item_id: itemId }),
  });
}

export function saveDeskPlan(
  sessionId: string,
  version: number,
  items: { id: string; name: string; start: string; end: string; remove: boolean }[],
): Promise<{ plan: DeskPlan | null }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/plan`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ version, items }),
  });
}

export function sayOnDesk(sessionId: string, text: string): Promise<{ id: string }> {
  return request(`/api/admin/desk/${encodeURIComponent(sessionId)}/say`, {
    method: "POST",
    headers: deskHeader(),
    body: JSON.stringify({ text }),
  });
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
    /** Edits come back as ticks in the chat. The plan changes only after one is accepted. */
    review?: boolean;
    /** Images and PDFs sent with the message; `data` is base64 without a `data:` prefix. */
    attachments?: ChatAttachment[];
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
  // A line ends with LF, CR or CRLF. After a CR at the end of a chunk, an LF opening the next chunk is the other half of a CRLF.
  let afterCR = false;
  // The server ends every reply with `done`, or with `error` when it failed.
  let finished = false;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    let text = decoder.decode(value, { stream: true });
    if (!text) continue;
    if (afterCR && text.startsWith("\n")) text = text.slice(1);
    afterCR = text.endsWith("\r");
    buffer += text.replace(/\r\n?/g, "\n");
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
      if (event === "done" || event === "error") finished = true;
      try {
        handlers.onEvent(event, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
      } catch (error) {
        handlers.onError?.(error instanceof Error ? error : new Error(String(error)));
      }
    }
  }
  // A body that ends with neither is a page a proxy answered with, or a connection cut short: nothing says what became of
  // the turn, so it failed (unless the traveller stopped it).
  if (!finished && !signal?.aborted) throw new ApiError("The reply ended before the server finished it.", 0);
}

export type { ClarifyPayload, EvalRun, PlanLocale, PlanTranslation, RunTrace, TripPlan };
