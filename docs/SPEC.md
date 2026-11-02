# Functional specification

This is the group's working specification. It states only what the product must do
and leaves implementation choices to the code that follows.

## 1. One-sentence definition

A conversational, state-aware agentic trip planner: the traveller describes a trip
in natural language, the system gathers live travel data with tools, assembles a
validated day-by-day itinerary with sources, and keeps refining it through
dialogue without redoing work that did not change.

## 2. Five obligations

1. **Complete itinerary** — attractions, hotel, daily activities, weather for the
   travel dates, and train/flight tickets where a source exists.
2. **Three intents, one path each** — `plan`, `modify`, `ask`; anything unclear is
   clarified, never guessed.
3. **Minimal edits** — only the affected parts are recomputed; confirmed and
   unaffected items are preserved verbatim.
4. **Provenance** — every fact traces to a tool or page returned for this trip.
   Missing data is `unavailable`, never invented.
5. **Memory** — trips marked useful are saved and can seed a later similar trip,
   but never bypass current-trip verification.

## 3. Turn pipeline

```
message -> screen (injection flags) -> fence as data
        -> route (typed: intent, confidence, need_clarification, reason, affected_parts)
        -> gate (threshold 0.8; missing slots; plan required for modify/ask)
        -> one path
plan   : retrieve memory -> 4 agents in parallel under a time budget
         -> planner (grounded composition) -> validate -> bounded repair (<=2)
modify : extract typed change -> rerun only affected agents
         -> merge preserving confirmed items -> validate -> version + 1
ask    : answer from current plan if it holds a fresh fact
         -> else exactly one allowlisted tool -> grounded answer
clarify: one short question about the missing slots; no tools, no planning
```

## 4. Four pre-planning agents

| Agent | Must collect | Fallback behaviour |
|---|---|---|
| Attraction | named places, category, description, coordinates, rating/hours when available, >=1 source per place | Google Places/Nominatim/Wikivoyage, then `unavailable` |
| Hotel | candidates with price (reference or live), rating, distance to centre | other hotel source, then `unavailable` |
| Weather | daily forecast for the trip dates | outside forecast horizon -> `unavailable` with reason |
| Ticket | flight/rail options with times and reference fares; reservation flags | `unavailable`; never estimate a fare |

All four run concurrently. A slow or failed agent marks only its section
`unavailable`; the plan still returns inside the budget.

## 5. Data rules

- Every plan item has `source_refs[]`, `data_status` (`ok|stale|unavailable`) and
  `fetched_at`.
- Freshness windows: weather 6 h, tickets/fares 24 h, hotels 7 days, attractions
  30 days; stale data is refreshed by the responsible agent only.
- Search notes are restricted to the last 24 months when a date is available;
  undated results rank lower.
- Distinct states: `no_data` (the source answered, nothing matched) versus a tool
  failure (timeout, rate limit, bad request, auth). The UI copy differs.
- Model output may not fill a missing fact. The planner composes items from
  provider DTOs and validates every item id it returns.

## 6. State and memory

- Server-side `TripPlan` is the single source of truth; the browser renders it.
- Every successful edit increments `version`; old versions are retained.
- Feedback (`useful`, optional 1–5 stars, optional reason code) is saved.
- A useful trip is embedded and stored with destination/rating. New plans retrieve
  candidates above a similarity cutoff (default 0.72), rerank, and inject only the
  summary/preferences — never facts without current verification.
- Preferences expressed in chat are session memory first, then persisted in the
  background. No personal identity data is stored.

## 7. Security

- User text is data, fenced in the prompt, never followed as an instruction.
- A blocklist screens prompt overrides, hidden-instruction requests and smuggled
  tool calls; suspected injections are logged in aggregate (category + count only).
- Every path has a tool allowlist; `ask` can never start pre-planning agents.
- A canary token is planted in every system prompt; if it appears in any output the
  turn is blocked and the event logged.
- The agent never books, pays, cancels or reads an account.

## 8. Screens

English-first console with four surfaces:

1. **Plan** — three columns: trip/chat on the left, itinerary workspace in the
   centre, live light-gray map + place card + budget on the right.
2. **Saved trips** — useful trips and version history.
3. **Evaluation** — metric cards, experiment table, trends, dataset split, failure
   drill-down, export; read-only.
4. **Settings** — thresholds, freshness windows, time budget, model/version
   snapshot; read-only plus local admin edit.

Every async surface has empty, loading and error states. Phone layout stacks to one
column with no horizontal scrolling at 390 px.

## 9. Acceptance checks (automated)

- one turn takes exactly one path;
- `ask` never runs a pre-planning agent;
- confidence below threshold always clarifies;
- modify preserves locked/confirmed items and increments version;
- unavailable data is never replaced by model knowledge;
- schema failure after two repairs returns an error, never a dirty plan;
- weak memory matches are not injected;
- canary leakage is blocked;
- per-path tool allowlists are enforced;
- the evaluation dashboard renders two runs and their delta without manual data.
