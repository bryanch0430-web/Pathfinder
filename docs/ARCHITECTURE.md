# Architecture

## 1. Design goals

1. **One path per turn.** Routing is typed and cheap; planning never runs for a question.
2. **Grounding over fluency.** The domain refuses a sourced-looking fact with no source.
3. **Minimal edits.** A change reruns only the agents that own it and preserves the rest.
4. **Replaceable edges.** Every vendor is behind a port; the app can boot with no keys.
5. **A visible failure is better than a fabricated success.**

## 2. Dependency rule

```
interface ──▶ application ──▶ domain ◀── infrastructure
                  │                          │
                  └──── ports (Protocol) ────┘
```

- `domain/` has no framework, SDK, HTTP or DB import.
- `application/` imports domain ports and models. It never imports FastAPI or SQLite.
- `infrastructure/` implements the ports (LLM, Jev, providers, store, tracing).
- `interface/` maps HTTP/SSE to `TurnService`.
- `container.py` is the composition root and the only module aware of every concrete.

`application/toolbox.py` is the single choke point for provider calls. It enforces a
per-path allowlist, measures latency, writes tool-call logs and lets a provider failure
propagate as a typed `ProviderError`.

## 3. Domain vocabulary

| Entity | Responsibility | Key invariants |
|---|---|---|
| `TripContext` | Form + dialogue slots | `missing_slots()` drives clarification; dates normalise to `days` |
| `RouteDecision` | Typed router output | intent ∈ {plan, modify, ask, unclear}; confidence 0–1 |
| `TripPlan` | The itinerary document | days cover the date range exactly; item ids unique; cost non-negative |
| `DayPlan` | One date | items do not overlap; weather is for that date |
| `PlanItem` | One stop | attraction must have ≥1 source; end > start |
| `HotelStay`, `TicketOption` | Bookable decisions | reference/live price kind is explicit |
| `AgentResult` | One agent's output | status + failures + sources + elapsed; never raw prose |
| `DataGap` | A missing section | distinguishes `no_data` from timeout/rate-limit/auth |
| `SavedTrip` | Long-term memory | destination, rating, embedding, summary |
| `EvaluationRun` | One scored experiment | frozen dataset, model snapshot, metrics, cases |

Freshness (`ok | stale | unavailable`) is carried by every weather row and item.

## 4. Turn sequence

```
TurnService.handle_turn
  screen_message            → categories + severity; high severity blocks
  store user message
  understand(message)       → deterministic rules; writer only fills empty slots
  save context
  load current plan
  System1Router.route       → Jev → writer → rules; gate applied to all
  emit route
  clarify | plan | modify | ask
  persist assistant message / plan / version
  emit trace + done
```

Live events are pushed through an `asyncio.Queue` while the path runs, so agent
start/finish frames reach the browser before the plan is ready.

### Plan path

1. Geocode the destination through the geocoder chain.
2. Start `AttractionAgent`, `HotelAgent`, `WeatherAgent`, `TicketAgent` concurrently.
3. `asyncio.wait` with `preplan_budget_seconds`; unfinished agents are cancelled and
   converted to `AgentResult(status=UNAVAILABLE, DataGap(TIMEOUT))`.
4. `Planner.compose` builds candidate ids; the writer (if present) may only return
   `{summary, day_titles, assignments, hotel_id}`; every id is validated.
5. Deterministic schedule assigns times, travel estimates, meals and cost.
6. `validate_plan`; on failure `repair_plan` (≤2) with a grounding check that forbids new
   names and new source URLs; otherwise `PlanInvalidError`.
7. Persist; version 1.

### Modify path

1. Typed `ChangeDraft` from the writer, or rules over the router's `affected_parts`. The rules read a request
   for other dates or another length in any wording (`shift the trip by a week`, `two days later`,
   `make it 4 days`) as a modify with the part `dates`, never as a new trip; a day, a night or `later` that
   names part of the trip (`add a temple on day 2`, `a later flight`) is not a new date.
2. Map parts to agents; a budget change reruns hotel; a date change reruns
   weather/hotel/ticket. If the date length changes, the plan is rebuilt with the same
   `trip_id` and version + 1. In both cases the hotel the plan has is kept: it is priced again
   for the new nights and keeps its confirmed and locked marks, and when no new rate comes
   back it keeps its old rate and a data gap says so. Another hotel is chosen only when the
   message asks for one or for a lower budget.
3. Only unlocked, unconfirmed items are replaced. When the trip changes length and is built again, each locked or
   confirmed stop comes back as the traveller left it (marks and times), once, on the day it was on (the last
   day when the trip is shorter, never a day kept free). The rebuild's own row for the same place goes, a stop
   that would overlap another slides to the next free time, and the plan says where each stop was kept. Their
   transfers and the plan's checks are read again.
4. Recalculate cost and constraints; validate/repair; persist version + 1.
5. `diff_plans` reports added/removed/changed items, a trip that moved (`dates`), the hotel's nights
   and rate (`stay`, `hotel_price`), a different hotel or flight, a changed forecast and the cost.

A guard keeps the plan's dates when the trip form holds other dates (it was saved, not generated)
and the message does not ask for other dates. This prevents a hotel swap, or `add a temple on day 2`,
from silently moving the trip; the form keeps the dates until the page's `Re-plan with the updated
details.` or a request such as `change the dates` applies them. Naming a hotel or a stop, or having
one selected, in such a request does not turn it into a single-row edit.

### Ask path

1. Search the current plan for weather, hotel, flight, cost or a named item.
2. If found, answer from the plan; zero tools.
3. Otherwise exactly one allowlisted tool: weather, route or place search.
4. Answer through the writer with the tool result; deterministic fallback keeps it
   grounded if the writer is offline. The plan is never modified.

## 5. Provider registry

`ProviderRegistry.build(settings)` creates priority-ordered lists:

- geocoder: Google (only when a Places or Maps key is set) → Open-Meteo → Nominatim
- weather: Open-Meteo forecast → Open-Meteo Archive (3-year seasonal reference, `stale`)
- places: Google Places → Nominatim OpenStreetMap → Wikivoyage (compliant Wikimedia User-Agent; geosearch dropped as low-precision)
- hotels: SerpApi Google Hotels → Wikivoyage Sleep
- flights: SerpApi Google Flights
- notes: YouTube → Bilibili
- web search: DeepSeek Search; Grok search is a second, opt-in list (`GROK_SEARCH_ENABLED`)
- routes: Google Routes
- timezone, elevation and destination autocomplete: Google Time Zone, Elevation and Places
  Autocomplete (single sources, each behind its own switch or key)
- FX: open.er-api.com with a dated static fallback

An agent walks the list; the first usable result wins. `no_data` means “the source
answered, nothing matched”; a timeout/rate-limit/auth/parse error becomes a `DataGap`
with that kind. The UI copy differs.

Adding a source: implement the protocol, append it to one list in the registry, and add
its credential to `config.py` / `.env.example`. No use case changes.

## 6. Persistence and memory

SQLite (WAL) tables: `accounts` (email, scrypt password hash, display name), `journeys`
(the session; `user_id` is set once an account claims it), `journey_state` (the trip
context), `turns` (messages), `itineraries` (one row per plan version), `ratings`,
`kept_journeys` (trips marked useful, with their embedding), `share_links`,
`taste_profiles` (preferences per profile token), `tool_spans`, `threat_events`,
`eval_suites` and `eval_trials`.
`application/trending.py` aggregates the newest plan version per trip into public cards
(destination, count, days, year, sight names); it never reads an owner, budget or token.
`itineraries` is keyed by `(session_id, trip_id, version)`, so history survives edits.

Memory recall: a signed hashing embedding (192 dims) over destination, days, party and
preferences. Cosine similarity + a destination bonus; only matches ≥
`memory_similarity_cutoff` are injected. The planner receives summaries only, never old
facts as current truth. This is the documented single-node replacement for pgvector.

## 7. Security

| Control | Location |
|---|---|
| Blocklist + severity | `security/guard.py` |
| Data fence | `fence_user_message` |
| Canary | prompt composition + `LLMGateway` check |
| Tool allowlists | `application/toolbox.py` |
| Aggregate injection log | `store.log_injection` (category + count only) |
| No transacting tools | no booking provider is registered |

The canary propagates as `CanaryLeak` through agent/planner/clarify call sites; the turn
is converted into a safe error and logged.

## 8. Observability

Every turn has a `TraceRecorder`: spans for understand/route/geocode/agents/tools/planner,
generation records (purpose, model, tokens, latency) and metadata (intent, confidence,
memory match scores). JSONL is always written to `data/traces/<request_id>.json`.
When Langfuse keys exist, a background thread posts trace/span/generation records to the
ingestion API. Observability failures never break a turn.

## 9. Frontend architecture

```
design-system/   tokens + atoms/elements/molecules/organisms/templates + stories
app/             pages (Planner composition, saved, evaluation, settings)
lib/             api client (fetch + SSE), usePathfinder hook, i18n
```

- `design-system` has no imports from `app/` or `lib/`; it is publishable on its own.
- The application composes Storybook components; no layout CSS is written in pages.
- `usePathfinder` owns the session lifecycle, optimistic messages, SSE parsing, live
  agent state and canonical refresh after every turn.
- The SSE client parses `event:`/`data:` frames and routes them into the hook.
- All screens have empty/loading/error states.

## 10. Trade-offs

| Choice | Why | Cost |
|---|---|---|
| Deterministic planner + optional model | grounding is enforceable; latency is predictable | themes are less creative than free-form generation |
| SQLite + hashed embedding | zero infrastructure, offline tests | no true semantic recall at scale |
| Google Maps | one browser key, one provider, works everywhere | needs network and a valid key; not navigable |
| Distance-estimated transport modes | no extra routing quota, mode changes visible | not a live route; every leg is labelled `est` |
| Workspace-local trending | real aggregate without a social database | only plans in this SQLite workspace; small sample |
| One writer for many roles | simple configuration | the same vendor can behave similarly across roles |
| English-first | product clarity and evaluation consistency | Chinese is a chrome translation, not full localisation |
