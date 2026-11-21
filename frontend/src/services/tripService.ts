import { apiFetch, getAuthHeaders, API_BASE_URL, errorText } from './apiClient';
import type { Coordinates, ItineraryGraph, NearbyPlace, RoadMetric } from '../components/create/itineraryGraph';
import type { OnboardingValues } from '../components/create/TripOnboardingForm';

export async function getItineraryGraph(tripId: string): Promise<ItineraryGraph> {
  const response = await apiFetch<ItineraryGraph>(`/api/trips/${tripId}/itinerary-graph`, {
    method: 'POST',
  });
  if (!response.ok || !response.data) throw new Error(response.error || 'Could not load itinerary graph');
  return response.data;
}

export interface TripCreateResponse {
  trip_id: string;
  session_id: string;
  assistant_message: {
    id: string;
    role: string;
    message_type: string;
    content: string;
    payload?: any;
    created_at: string;
  };
}

export interface TripModelResponse {
  id: string;
  user_id?: string | null;
  guest_id?: string | null;
  destination?: string | null;
  places_to_visit: string[];
  planning_preferences?: Partial<OnboardingValues['planning_preferences']> & { useful?: boolean | null; stars?: number | null };
  origin_text?: string | null;
  origin_latitude?: number | null;
  origin_longitude?: number | null;
  duration_days?: number | null;
  currency?: string;
  status: string;
  onboarding_status: string;
  created_at: string;
  updated_at: string;
}

export type BudgetCategory = 'travel' | 'stay' | 'food' | 'activities' | 'other';
export type BudgetCurrency = string;

export interface BudgetItemInput {
  label: string;
  category: BudgetCategory;
  place_name: string | null;
  quantity: string;
  unit_amount: string | null;
  is_included: boolean;
}

export interface BudgetItem extends BudgetItemInput {
  id: string;
  source_key: string | null;
  scope: 'stop' | 'leg' | 'manual';
  quote_text: string | null;
  /** Suggested amount per unit; never counted until accepted into unit_amount. */
  estimate_amount: string | null;
  estimate_note: string | null;
  is_current: boolean;
  updated_at: string;
}

export interface TripBudget {
  trip_id: string;
  currency: BudgetCurrency;
  target_amount: string | null;
  is_locked: boolean;
  locked_at: string | null;
  locked_total: string | null;
  priced_total: string;
  remaining: string | null;
  unpriced_count: number;
  /** Entered amounts plus suggestions for rows still missing one. */
  projected_total: string;
  unestimated_count: number;
  review_count: number;
  category_totals: Record<string, string>;
  place_totals: Record<string, string>;
  items: BudgetItem[];
}

async function budgetRequest(tripId: string, path: string, method: string, body?: unknown): Promise<TripBudget> {
  const response = await apiFetch<TripBudget>(`/api/trips/${tripId}/budget${path}`, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok || !response.data) throw new Error(response.error || 'Could not update the trip budget');
  return response.data;
}

export const getTripBudget = (tripId: string) => budgetRequest(tripId, '', 'GET');
export const updateBudgetSettings = (tripId: string, body: { currency: BudgetCurrency; target_amount: string | null }) =>
  budgetRequest(tripId, '/settings', 'PUT', body);
export const addBudgetItem = (tripId: string, body: BudgetItemInput) =>
  budgetRequest(tripId, '/items', 'POST', body);
export const updateBudgetItem = (tripId: string, itemId: string, body: BudgetItemInput) =>
  budgetRequest(tripId, `/items/${itemId}`, 'PUT', body);
export const deleteBudgetItem = (tripId: string, itemId: string) =>
  budgetRequest(tripId, `/items/${itemId}`, 'DELETE');
/** The normalized trip every view and export reads (backend `GET /trips/{id}/document`). */
export interface TripDocumentItem {
  name: string;
  category: string;
  time_of_day: 'morning' | 'afternoon' | 'evening' | null;
  /** Planning slot copied from the itinerary. Absent on older documents. */
  start?: string | null;
  end?: string | null;
  area: string | null;
  est_cost: number | null;
  duration_hours: number | null;
  tip: string | null;
  source_url: string | null;
  hours?: string | null;
  option: boolean;
}

export interface TripNote { tone: 'good' | 'warn' | 'info'; text: string }
export interface RouteWarning { day: number; severity: 'impossible' | 'tight' | 'unverified'; message: string;
  travel_hours: number; distance_km: number; basis: string }

/** Weather, holidays and exchange rates around the trip's dates (backend `services/enrichment.py`). */
export interface TripEnrichment {
  /** The days' base cities, located on OpenStreetMap (optional so fixtures can leave it out). */
  places?: { name: string; lat: number; lon: number; country: string | null }[];
  weather: {
    day: number;
    date: string;
    base: string;
    /** A real forecast, or the month's climate average: always show `label` with the numbers. */
    kind: 'forecast' | 'typical';
    label: string;
    condition: 'sun' | 'partly' | 'cloud' | 'fog' | 'rain' | 'snow' | 'storm' | null;
    temp_min: number | null;
    temp_max: number | null;
    temp_mean: number | null;
    /** Forecast: that day's total. Typical: the average per day. */
    rain_mm: number | null;
    badge: TripNote | null;
    notes: TripNote[];
  }[];
  holidays: { day: number; date: string; name: string; local_name: string | null; country: string; regional: boolean }[];
  /** 1 `base` = rate × currency, ECB reference rates. */
  exchange: { base: string; rates: Record<string, number>; date: string } | null;
  sources: { name: string; url: string; covers: string }[];
}

export interface TripDocument {
  trip_id: string;
  mode: 'agent' | 'one_shot' | 'none';
  is_final?: boolean;
  status: 'building' | 'complete' | null;
  destination: string | null;
  origin: string | null;
  duration_days: number | null;
  start_date: string | null;
  end_date: string | null;
  travelers: { adults: number; children: number };
  comfort: string;
  travel_mode: string;
  pace: string;
  interests: string[];
  avoid: string[];
  must_see: string[];
  guide: string | null;
  days: { day: number; date: string | null; base: string; items: TripDocumentItem[]; est_cost: number | null; hours: number | null;
    travel_hours?: number | null; travel_distance_km?: number | null; travel_basis?: string | null; unverified_legs?: number }[];
  route_warnings?: RouteWarning[];
  legs: { source: string; target: string; mode: string | null; duration: string | null; distance: string | null; cost: string | null }[];
  budget: { currency: string; target: number | null; planned: number | null; entered: number; projected: number;
    is_locked?: boolean; locked_total?: number | null };
  graph: ItineraryGraph | null;
  /** Null when every source failed, enrichment is off, or the trip has nothing to look up yet. */
  enrichment: TripEnrichment | null;
}

export async function getTripDocument(tripId: string): Promise<TripDocument> {
  const response = await apiFetch<TripDocument>(`/api/trips/${tripId}/document`, { method: 'GET' });
  if (!response.ok || !response.data) throw new Error(response.error || 'Could not load the trip');
  return response.data;
}

export async function finalizeTrip(tripId: string): Promise<TripModelResponse> {
  const response = await apiFetch<TripModelResponse>(`/api/trips/${tripId}/finalize`, { method: 'POST' });
  if (!response.ok || !response.data) throw new Error(response.error || 'Could not finalize the trip');
  return response.data;
}

export const estimateBudget = (tripId: string) => budgetRequest(tripId, '/estimates', 'POST');
export const acceptBudgetEstimates = (tripId: string) => budgetRequest(tripId, '/estimates/accept', 'POST');
export const lockTripBudget = (tripId: string, acceptSuggestions = false) =>
  budgetRequest(tripId, '/lock', 'POST', { accept_suggestions: acceptSuggestions });
export const unlockTripBudget = (tripId: string) => budgetRequest(tripId, '/unlock', 'POST');

export async function getRoadMetrics(
  tripId: string,
  legs: { id: string; from: Coordinates; to: Coordinates }[],
): Promise<RoadMetric[]> {
  if (!legs.length) return [];
  const response = await apiFetch<{ provider: string; legs: RoadMetric[] }>(
    `/api/trips/${tripId}/route-metrics`,
    {
      method: 'POST',
      body: JSON.stringify({ legs: legs.map((leg) => ({
        id: leg.id,
        from_lat: leg.from.lat, from_lon: leg.from.lon,
        to_lat: leg.to.lat, to_lon: leg.to.lon,
      })) }),
    },
  );
  return response.ok ? response.data?.legs || [] : [];
}

export async function getTripGeocodes(
  tripId: string,
  places: { id: string; name: string; is_origin: boolean }[],
): Promise<Record<string, Coordinates>> {
  if (!places.length) return {};
  const response = await apiFetch<{ provider: string; places: { id: string; lat: number; lon: number }[] }>(
    `/api/trips/${tripId}/geocode`,
    { method: 'POST', body: JSON.stringify({ places }) },
  );
  return Object.fromEntries((response.ok ? response.data?.places || [] : [])
    .map((place) => [place.id, { lat: place.lat, lon: place.lon }]));
}

export async function getNearbyPlaces(tripId: string, point: Coordinates): Promise<NearbyPlace[]> {
  const response = await apiFetch<{ provider: string; places: Omit<NearbyPlace, 'source'>[] }>(
    `/api/trips/${tripId}/nearby-places`,
    { method: 'POST', body: JSON.stringify({ lat: point.lat, lon: point.lon }) },
  );
  if (response.data?.provider === 'limit_reached') {
    throw new Error('Live nearby search reached its demo limit. Places from your draft are still available.');
  }
  return response.ok && response.data?.provider === 'google'
    ? response.data.places.map((place) => ({ ...place, source: 'google' as const })) : [];
}

export interface TripStateResponse {
  trip: TripModelResponse;
  conversation: {
    id: string;
    trip_id: string;
    status: string;
    current_stage: string;
    context_summary?: string | null;
    created_at: string;
    updated_at: string;
  };
  assistant_message?: {
    id: string;
    role: string;
    message_type: string;
    content: string;
    payload?: any;
    created_at: string;
  };
}

export interface StreamMetadataPayload {
  destination?: string | null;
  duration_days?: number | null;
  origin?: string | null;
  onboarding_complete?: boolean;
  missing_fields?: string[];
  user_name?: string | null;
}

export interface StreamDonePayload {
  trip: TripModelResponse;
  conversation: {
    id: string;
    trip_id: string;
    status: string;
    current_stage: string;
    context_summary?: string | null;
    created_at: string;
    updated_at: string;
  };
  assistant_message?: {
    id: string;
    role: string;
    message_type: string;
    content: string;
    payload?: any;
    created_at: string;
  };
}

export interface StreamActionEventPayload {
  type: 'action';
  action: string;
  payload?: any;
}

export interface SendTripMessageStreamCallbacks {
  onToken: (delta: string) => void;
  onGraph?: (graph: ItineraryGraph, final: boolean) => void;
  /** Build-with-agent state, sent as soon as a decision is applied (before the reply streams). */
  onCopilot?: (copilot: any) => void;
  onMetadata?: (meta: StreamMetadataPayload) => void;
  onStage?: (label: string) => void;
  onAction?: (actionEvent: StreamActionEventPayload) => void;
  onDone?: (data: StreamDonePayload) => void;
  onError?: (err: Error) => void;
}

/**
 * Initialize a new conversational trip session belonging to the current user or guest.
 */
export async function createTrip(): Promise<TripCreateResponse | null> {
  const res = await apiFetch<TripCreateResponse>('/api/trips', {
    method: 'POST',
  });

  if (!res.ok || !res.data) {
    throw new Error(res.error || 'Failed to create trip');
  }

  return res.data;
}

/**
 * List all trips for the authenticated user or active guest.
 */
export async function listTrips(): Promise<TripModelResponse[]> {
  const res = await apiFetch<TripModelResponse[]>('/api/trips', {
    method: 'GET',
  });

  if (!res.ok || !res.data) {
    return [];
  }

  return res.data;
}

/**
 * Retrieve current trip state and active conversational session.
 */
export async function getTrip(tripId: string): Promise<TripStateResponse | null> {
  const res = await apiFetch<TripStateResponse>(`/api/trips/${tripId}`, {
    method: 'GET',
  });

  if (!res.ok || !res.data) {
    throw new Error(res.error || 'Failed to get trip details');
  }

  return res.data;
}

/**
 * Send user message or UI action to the trip agent (synchronous).
 */
export async function sendTripMessage(
  tripId: string,
  content: string,
  messageType: 'TEXT' | 'UI_ACTION' = 'TEXT',
  payload?: Record<string, any>
): Promise<TripStateResponse | null> {
  const res = await apiFetch<TripStateResponse>(`/api/trips/${tripId}/messages`, {
    method: 'POST',
    body: JSON.stringify({
      message_type: messageType,
      content,
      payload: payload || null,
    }),
  });

  if (!res.ok || !res.data) {
    throw new Error(res.error || 'Failed to send message');
  }

  return res.data;
}

/**
 * Send user message to the trip agent via Server-Sent Events (SSE) streaming.
 * Provides real-time token streaming, live metadata extraction, and final state persistence.
 */
export async function sendTripMessageStream(
  tripId: string,
  content: string,
  callbacks: SendTripMessageStreamCallbacks,
  messageType: 'TEXT' | 'UI_ACTION' = 'TEXT',
  payload?: Record<string, any>
): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const headers = new Headers(authHeaders);
  headers.set('Content-Type', 'application/json');

  const url = `${API_BASE_URL}/api/trips/${tripId}/messages/stream`;
  const controller = new AbortController();
  let timedOut = false;
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  const resetIdleTimer = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { timedOut = true; controller.abort(); }, 90000);
  };
  resetIdleTimer();
  try {
  const res = await fetch(url, {
    method: 'POST',
    headers,
    signal: controller.signal,
    body: JSON.stringify({
      message_type: messageType,
      content,
      payload: payload || null,
    }),
  });

  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const errJson = await res.json();
      errMsg = errorText(errJson.detail ?? errJson.message, errMsg);
    } catch (_) {}
    const err = new Error(errMsg);
    callbacks.onError?.(err);
    throw err;
  }

  if (!res.body) {
    const err = new Error('No response body for streaming');
    callbacks.onError?.(err);
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let receivedDone = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      resetIdleTimer();

      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const block of lines) {
        const trimmed = block.trim();
        if (!trimmed) continue;
        const lineList = trimmed.split('\n');
        for (const line of lineList) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6).trim();
            if (!dataStr) continue;
            let event: any;
            try { event = JSON.parse(dataStr); }
            catch (jsonErr) { console.warn('Failed to parse SSE JSON:', dataStr, jsonErr); continue; }
              if (event.type === 'token' && event.delta) {
                callbacks.onToken(event.delta);
              } else if (event.type === 'graph' && event.graph) {
                callbacks.onGraph?.(event.graph as ItineraryGraph, Boolean(event.final));
              } else if (event.type === 'copilot' && event.copilot) {
                callbacks.onCopilot?.(event.copilot);
              } else if (event.type === 'action') {
                callbacks.onAction?.(event);
              } else if (event.type === 'metadata') {
                callbacks.onMetadata?.(event);
              } else if (event.type === 'stage' && typeof event.label === 'string') {
                callbacks.onStage?.(event.label);
              } else if (event.type === 'done') {
                receivedDone = true;
                callbacks.onDone?.(event);
              } else if (event.type === 'error') {
                throw new Error(errorText(event.error, 'The planner could not finish. Please try again.'));
              }
          }
        }
      }
      if (receivedDone) break;
    }
    if (!receivedDone) throw new Error('The planner stopped before saving the answer. Please try again.');
  } catch (streamErr: any) {
    const error = timedOut ? new Error('The planner took too long. Please try again.') : streamErr;
    callbacks.onError?.(error);
    throw error;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  } finally { clearTimeout(idleTimer); }
}

export interface ConversationMessageApiItem {
  id: string;
  session_id: string;
  role: string;
  message_type: string;
  content: string;
  payload?: any;
  created_at: string;
}

/**
 * Retrieve all conversation messages for a trip.
 */
export async function getTripMessages(tripId: string): Promise<ConversationMessageApiItem[]> {
  const res = await apiFetch<{ messages: ConversationMessageApiItem[] }>(
    `/api/trips/${tripId}/messages`,
    { method: 'GET' }
  );

  if (!res.ok || !res.data) {
    return [];
  }

  return res.data.messages;
}

/**
 * Delete a trip and its messages from the database.
 */
export async function deleteTrip(tripId: string): Promise<boolean> {
  const res = await apiFetch<{ success: boolean; trip_id: string }>(
    `/api/trips/${tripId}`,
    { method: 'DELETE' }
  );

  return res.ok;
}

export async function markTripUseful(tripId: string, useful: boolean | null, stars?: number | null): Promise<{ useful: boolean | null; stars: number | null } | null> {
  const res = await apiFetch<{ useful: boolean | null; stars: number | null }>(`/api/trips/${tripId}/feedback`, {
    method: 'POST',
    body: JSON.stringify(useful && stars ? { useful, stars } : { useful }),
  });
  return res.ok && res.data ? res.data : null;
}

export async function undoTripPlan(tripId: string): Promise<boolean> {
  const res = await apiFetch(`/api/trips/${tripId}/undo`, { method: 'POST' });
  return res.ok;
}

