export type DataStatus = "ok" | "stale" | "unavailable";
export type ItemType = "attraction" | "hotel" | "meal" | "transit" | "ticket";
export type Intent = "plan" | "modify" | "ask" | "unclear";

export interface GeoPoint {
  lat: number;
  lng: number;
}

export interface Money {
  amount: number;
  currency: string;
}

export interface SourceRef {
  title: string;
  url: string;
  source: string;
  fetched_at?: string;
}

export interface WeatherDay {
  date: string;
  status: DataStatus;
  summary: string;
  high_c: number | null;
  low_c: number | null;
  precipitation_probability: number | null;
  source: string;
  fetched_at?: string;
  note?: string;
}

export interface PlanItem {
  id: string;
  type: ItemType;
  name: string;
  start: string;
  end: string;
  geo: GeoPoint | null;
  address: string;
  notes: string;
  source_refs: SourceRef[];
  data_status: DataStatus;
  fetched_at?: string;
  price: Money | null;
  price_kind: "live" | "reference" | "estimate" | "none";
  booking_url?: string;
  locked: boolean;
  confirmed: boolean;
  tags: string[];
  /** The place source's own hours for the visit day; "unknown" when it gave none. */
  opening_hours?: string[];
  hours_status?: "stated" | "unknown";
  /** The trip from the previous stop, as the routes tool returned it. */
  transfer?: TransferLeg | null;
  /** Trusted localized names from the sources ("en", "zh-Hant"); never written by a model. */
  names?: Record<string, string>;
  /** Which meal a meal stop is; absent on other items. */
  meal_slot?: MealSlot | null;
  /** Facts as the place source gave them; null when it gave none. */
  rating?: number | null;
  review_count?: number | null;
  price_level?: string | null;
  cuisine?: string | null;
  /** Only set when the source said whether it takes reservations. */
  reservable?: boolean | null;
  /** The place source's own type labels and short description; empty when it gave none. */
  source_types?: string[];
  source_summary?: string;
  /** Other source-backed choices for this stop; the item itself is the default. */
  alternatives?: PlanAlternative[];
}

export type MealSlot = "breakfast" | "lunch" | "dinner" | "snack";

export interface PlanAlternative {
  id: string;
  name: string;
  geo: GeoPoint | null;
  address: string;
  rating: number | null;
  review_count: number | null;
  price_level: string | null;
  cuisine: string | null;
  reservable: boolean | null;
  opening_hours: string[];
  source_refs: SourceRef[];
  booking_url: string;
  /** Straight-line metres from the default stop, computed by the app. */
  distance_m: number | null;
  source_types?: string[];
  source_summary?: string;
}

export interface TransferLeg {
  from_item_id: string;
  minutes: number;
  mode: string;
  distance_km?: number | null;
  source: string;
  tool: string;
  result_id: string;
}

export interface DayPlan {
  date: string;
  title: string;
  weather: WeatherDay | null;
  items: PlanItem[];
  note: string;
  elevation_gain_m?: number | null;
  /** The city (segment) the traveller sleeps in that night; absent on plans from before cities. */
  segment_id?: string;
}

export interface RoomType {
  name: string;
  price_per_night: Money | null;
  source: string;
  link: string;
}

export interface HotelNearby {
  name: string;
  type: string;
  duration: string;
}

/** How far a stay meets the traveller's stay wishes, and what the sources showed for it. */
export interface StayMatch {
  /** The traveller's stay wishes, e.g. "a room with two beds". */
  wishes: string[];
  verdict: "meets" | "partly" | "unclear" | "fails";
  /** 0 to 100. */
  score: number;
  /** One sentence; may be empty. */
  reason: string;
  /** Room names, amenities or page titles. */
  evidence: string[];
  evidence_refs: { title: string; url: string; source: string }[];
  rooms_checked: boolean;
}

export interface HotelStay {
  id: string;
  name: string;
  area: string;
  geo: GeoPoint | null;
  check_in: string;
  check_out: string;
  price_per_night: Money | null;
  price_kind: "live" | "reference" | "estimate" | "none";
  rating: number | null;
  review_count: number | null;
  distance_to_centre_km: number | null;
  source_refs: SourceRef[];
  data_status: DataStatus;
  booking_url: string;
  notes: string;
  confirmed: boolean;
  locked: boolean;
  names?: Record<string, string>;
  amenities?: string[];
  hotel_class?: number | null;
  property_token?: string;
  nearby?: HotelNearby[];
  room_types?: RoomType[];
  /** The dates the hotel source was last asked for rooms with; `room_types` belong to them. */
  rooms_checked_for?: { start: string; end: string } | null;
  nearest_stop_km?: number | null;
  nearest_stop_name?: string;
  match?: StayMatch | null;
  /** The city (segment) this stay is in. */
  segment_id?: string;
}

/** The ways between two places a leg can be made by; `ground` is a rough estimate from the distance. */
export type TransportMode = "flight" | "train" | "ticket" | "ferry" | "bus" | "drive" | "ground";

export interface TicketOption {
  id: string;
  mode: TransportMode;
  /** The leg this option is for; absent on plans from before legs. */
  leg_id?: string;
  /** A duration worked out from a distance or a routes source, not a quoted fare and timetable. */
  estimated?: boolean;
  name: string;
  origin: string;
  destination: string;
  depart_at: string | null;
  arrive_at: string | null;
  duration_minutes: number | null;
  return_origin?: string;
  return_destination?: string;
  return_depart_at?: string | null;
  return_arrive_at?: string | null;
  return_duration_minutes?: number | null;
  return_airline?: string;
  return_flight_number?: string;
  price: Money | null;
  price_kind: "live" | "reference" | "estimate" | "none";
  operator: string;
  booking_url: string;
  notes: string;
  source_refs: SourceRef[];
  data_status: DataStatus;
  confirmed: boolean;
  selected?: boolean;
}

/** One city of the trip: `dates.start` is the arrival day, `dates.end` the departure day. */
export interface TripSegment {
  id: string;
  destination: { name: string; country_code: string; geo: GeoPoint | null; timezone: string };
  dates: { start: string; end: string };
}

/** One hop of the trip: from home to the first city, between two cities, or the way home. */
export interface TransportLeg {
  id: string;
  from_place: string;
  to_place: string;
  depart_date: string | null;
  kind: "outbound" | "between" | "return";
}

/** One city of the trip form, in travel order, with the nights the traveller gave it. */
export interface TripStop {
  city: string;
  nights: number | null;
}

export interface CostLine {
  label: string;
  amount: number;
  kind: string;
  note: string;
}

export interface CostBreakdown {
  currency: string;
  lines: CostLine[];
  total: number;
  budget: number | null;
  remaining: number | null;
  status: "within" | "over" | "unknown";
  notes: string[];
}

export interface ConstraintCheck {
  id: string;
  text: string;
  status: "pass" | "fail" | "unknown";
  evidence: string;
}

/** A forecast, fare or hotel price checked against its freshness window (Proposal DB-13). */
export interface FreshnessMark {
  section: "weather" | "tickets" | "hotels";
  status: "outdated" | "refreshed";
  fetched_at: string;
  checked_at: string;
  window_seconds: number;
  note: string;
}

export interface DataGap {
  section: string;
  kind: string;
  message: string;
  retryable: boolean;
}

export interface Reservation {
  id: string;
  name: string;
  reason: string;
  url: string;
  needed_by: string | null;
  status: "to_book" | "optional" | "booked";
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface ClientProfile {
  client_token: string;
  preferences: string[];
  hard_constraints: string[];
  sessions: number;
  saved_trips: number;
  destinations: string[];
  updated_at: string | null;
}

export interface GuideChunk {
  id: string;
  destination: string;
  section: string;
  text: string;
  title: string;
  url: string;
  score: number;
  country_code?: string;
  license?: string;
  last_verified?: string;
  stale?: boolean;
  content_hash?: string;
  corpus_version?: string;
  retrieval?: { mode: string; reranked: boolean; lexical_score?: number; dense_score?: number };
}

export interface FxSnapshot {
  base: string;
  rates: Record<string, number>;
  source: string;
  as_of: string;
  note: string;
}

export interface TrendingPlace {
  destination: string;
  plans: number;
  likes: number;
  days: number;
  year: number | null;
  places: string[];
  summary: string;
  match?: string[];
}

export interface TrendingTrace {
  note: string;
  filter: string;
  dropped: { destination: string; reason: string }[];
  mode?: string;
  candidates?: number;
}

export interface PlanDiffEntry {
  kind: "created" | "added" | "changed" | "removed" | "hotel" | string;
  item_id?: string;
  name?: string;
  from?: string;
  to?: string;
  detail?: string;
  /** The stay a `hotel`, `stay` or `hotel_price` entry is about. */
  id?: string;
  /** The leg a `transport` entry is about. */
  leg_id?: string;
  segment_id?: string;
  /** The city of a `city_added`, `city_removed` or `nights` entry. */
  city?: string;
}

export interface VideoNote {
  title: string;
  url: string;
  source: string;
  snippet?: string;
  video_id?: string;
  thumbnail_url?: string;
  published_at?: string | null;
}

/** How sure the planner is of a plan, and what would make it surer. */
export interface PlanAssessment {
  /** 0 to 1. */
  confidence: number;
  level: "high" | "medium" | "low";
  signals: { code: string; weight: number; message: string }[];
  /** What only the traveller can say. */
  missing: string[];
  /** What no source confirmed. */
  unverified: string[];
  next_action: "none" | "research" | "ask";
  /** May be empty. */
  question: string;
  research_rounds: number;
  /** What further research changed. */
  improvements: string[];
}

export interface TripPlan {
  schema_version: string;
  trip_id: string;
  session_id: string;
  version: number;
  status: "draft" | "ready";
  destination: { name: string; country_code: string; geo: GeoPoint | null; timezone: string };
  origin: string;
  dates: { start: string; end: string };
  party_size: number;
  budget: Money | null;
  summary: string;
  days: DayPlan[];
  hotels: HotelStay[];
  hotel_options?: HotelStay[];
  tickets: TicketOption[];
  /** The cities in travel order; absent on plans from before cities. */
  segments?: TripSegment[];
  /** The hops of the trip; absent on plans from before legs. */
  legs?: TransportLeg[];
  cost: CostBreakdown;
  constraints: ConstraintCheck[];
  disruptions: { id: string; kind: string; summary: string; affected_item_ids: string[]; resolution: string; resolved: boolean }[];
  reservations: Reservation[];
  video_notes?: VideoNote[];
  data_quality: {
    sources_used: string[];
    gaps: DataGap[];
    notes: string[];
    search_window_months: number;
    generated_at: string;
    freshness?: FreshnessMark[];
  };
  assessment?: PlanAssessment | null;
  change_summary: string;
  created_at: string;
  updated_at: string;
}

export interface TripContext {
  session_id: string;
  destination: string;
  origin: string;
  start_date: string | null;
  end_date: string | null;
  days: number | null;
  party_size: number | null;
  budget: number | null;
  currency: string;
  hotel_style: string;
  pace: string;
  preferences: string[];
  hard_constraints: string[];
  language: string;
  /** The language the plan is shown in; the English plan stays the source of truth. */
  plan_locale?: PlanLocale;
  /** The cities in travel order (empty for one city: `destination` is that city). */
  stops?: TripStop[];
  /** The trip does not return to where it starts. */
  one_way?: boolean;
}

/** Plan display languages. */
export type PlanLocale = "en" | "zh-Hant";

/** GET /api/journeys/{id}/translation: display text for one plan version. */
export interface PlanTranslation {
  locale: PlanLocale;
  plan_version: number;
  status: "translated" | "fallback" | "unavailable" | "english";
  reason: string;
  disclaimer: string;
  fields: {
    summary?: string;
    days?: Record<string, { title?: string; note?: string }>;
    /** Keyed by item id; only `notes` is translated. */
    items?: Record<string, { notes?: string }>;
    /** Keyed by the gap's index in `data_quality.gaps`. */
    gaps?: Record<string, string>;
  };
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  intent?: Intent | null;
  confidence?: number | null;
  created_at: string;
  meta?: Record<string, unknown>;
}

export interface AgentState {
  agent: "attraction" | "hotel" | "weather" | "ticket" | string;
  status: "running" | "ok" | "partial" | "unavailable" | "timeout" | "failed";
  elapsed_ms?: number;
  summary?: string;
}

export interface TraceSpan {
  name: string;
  duration_ms: number | null;
  status?: string | null;
}

export interface RunTrace {
  request_id: string;
  spans: TraceSpan[];
  generations: {
    purpose: string;
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
    latency_ms: number;
    reasoning?: string;
    output_preview?: string;
    user_preview?: string;
  }[];
  why?: string[];
  reason?: string;
  message?: string;
  searches?: {
    provider: string;
    query: string;
    results: { title?: string; url?: string; snippet?: string; relevance?: number; kept?: boolean; error?: string; text?: string }[];
  }[];
  tools?: { name: string; status: string; latency_ms: number; args?: Record<string, unknown>; note?: string }[];
  tool_calls: number;
}

export interface ClarifyPayload {
  question: string;
  slots: string[];
  labels: string[];
  options: Record<string, string[]>;
  /** concrete follow-ups when the question is about intent, not missing data */
  choices?: { label: string; message: string }[];
  reason_code: string;
}

/** One file sent with a chat message; `data` is the base64 of its bytes, without a `data:` prefix. */
export interface ChatAttachment {
  name: string;
  media_type: string;
  data: string;
}

/** The `intake` event: what was read from the links and files of a message, before it is routed. */
export interface IntakePayload {
  sources: { name: string; kind: "url" | "pdf" | "image" | "text"; status: "read" | "failed"; note: string }[];
  /** Trip form fields filled from the sources, e.g. "destination". */
  filled: string[];
  /** Place names taken from the sources. */
  places: string[];
}

export interface RoutePayload {
  intent: Intent;
  confidence: number;
  threshold: number;
  need_clarification: boolean;
  reason_code: string;
  affected_parts: string[];
  missing_slots: string[];
  related_to_plan: boolean;
  blocked?: boolean;
}

export interface EvalMetric {
  key: string;
  label: string;
  value: number;
  unit: string;
  baseline: number | null;
  delta: number | null;
  direction: "up" | "down" | "flat";
}

export interface EvalCaseResult {
  id: string;
  case_id: string;
  dataset: string;
  split: string;
  passed: boolean;
  score: number;
  failure_class: string;
  input: string;
  output_summary: string;
  judge_reason: string;
  judge_score?: number | null;
  judge_flags?: string[];
  tool_summary: string[];
  scores?: Record<string, number>;
  trace_id: string;
}

export interface EvalRun {
  id: string;
  experiment_tag: string;
  dataset_id: string;
  dataset_version: string;
  status: string;
  metrics: EvalMetric[];
  cases: EvalCaseResult[];
  model_snapshot: Record<string, unknown>;
  token_usage: number;
  latency_ms: number;
  created_at: string;
  finished_at: string | null;
}

export interface EvalRunSummary {
  id: string;
  experiment_tag: string;
  dataset_id: string;
  dataset_version: string;
  status: string;
  metrics: EvalMetric[];
  latency_ms: number;
  token_usage: number;
  created_at: string;
  case_count: number;
  passed: number;
}

export interface EvalDataset {
  id: string;
  name: string;
  version: string;
  split: string;
  frozen: boolean;
  item_count: number;
  created_at: string;
}
