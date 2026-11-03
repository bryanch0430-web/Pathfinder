# API

Base URL: `http://127.0.0.1:8100`. All bodies are JSON. Times are ISO 8601 UTC.

## 1. Meta

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ready` | process, writer, decision model, Langfuse, provider chains, thresholds |
| GET | `/api/runtime` | UI configuration, thresholds, freshness, provider chains, demo prompts |
| GET | `/api/handbook?destination=&q=&limit=&rerank=` | BM25+BGE hybrid by default; cited chunks with `last_verified`, `stale`, `content_hash`, `corpus_version`; `rerank=true` enables the optional BGE cross-encoder |
| GET | `/api/wallet?base=HKD` | cached live reference rates, source, date and note |
| GET | `/api/wallet/convert?amount=9000&from=HKD&to=USD` | converted amount and rate |
| GET | `/docs` | Swagger UI |

FX is keyless and cached for 12 hours. `open.er-api` is the live source; if it is
unreachable the dated static table is used and `source` becomes `static`. The UI labels
both and converts the known budget total into a chosen display currency.

`/api/ready` never proves every data key. A missing Places or SerpApi key appears as
a `data_quality.gap` on the plan, not a failed health check.

## 2. Auth and sessions

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/api/account/signup` | `{email,password,display_name?,client_token?,session_id?}` | `{token,user,profile_token}` |
| POST | `/api/account/signin` | same | `{token,user,profile_token}` |
| GET | `/api/account/whoami` | Bearer token | `{user,profile_token}` |
| POST | `/api/account/attach` | `{session_id}` | `{claimed}` |
| POST | `/api/journeys` | `{client_token}` | `{session, context}` |
| GET | `/api/journeys/{id}` | — | `{session, context, plan, versions, messages}` |
| PATCH | `/api/journeys/{id}/state` | `ContextPatch`: any `TripContext` field except `session_id`; `null` leaves a field unchanged | `{context}` |
| GET | `/api/journeys/{id}/itinerary` | `?version=` optional | `{plan}` |
| GET | `/api/journeys/{id}/revisions` | — | `{versions}` |
| GET | `/api/journeys/{id}/geometry` | — | map payload for the current plan |
| GET | `/api/journeys/{id}/segments` | `?day=YYYY-MM-DD` optional | `{trip_id, version, day, legs}` — real route geometry per leg |

Auth is optional. A guest session is readable by id; once a session is claimed by a user,
requests without the matching Bearer token return 404, not 403. `PATCH` recomputes `days`
from the date range (or `end_date` from `days`).

Shared trips are redacted before storage: no budget, party size, session id or client token
is exposed. The share URL is read-only and does not require an account.

### Map payload

```json
{
  "trip_id": "trip_...",
  "destination": "Kyoto",
  "center": {"lat": 35.0116, "lng": 135.7681},
  "hotel": {"name": "...", "lat": 35.0, "lng": 135.7},
  "days": [{"date": "2026-11-10", "title": "...", "weather": {...},
            "points": [{"id": "item_p1", "name": "...", "type": "attraction",
                        "lat": 34.99, "lng": 135.78, "start": "09:00",
                        "end": "10:30", "status": "ok", "sources": 2}]}],
  "tickets": [{"id": "flight_f1", "mode": "flight", "name": "...",
               "origin": "HKG", "destination": "KIX", "depart_at": "..."}]
}
```

## 3. Chat stream

`POST /api/turn/stream`

```json
{"session_id": "sess_...", "message": "Swap the hotel for the other option.", "locale": "en", "scope_item_id": "item_p3"}
```

`scope_item_id` is the stop behind the console's `About: …` chip. A scoped
`remove this` / `tell me about this` is resolved to that exact item; the server
forces the action/question path and skips the ambiguity clarification.

Response: `text/event-stream`. Each frame is `event: <name>` followed by a single-line
`data: <json>`.

| Event | Data |
|---|---|
| `route` | `{intent, confidence, threshold, need_clarification, reason_code, affected_parts, missing_slots, related_to_plan}` |
| `progress` | `{phase, message, progress}` |
| `agent` | `{agent, phase: "start"|"done", status?, elapsed_ms?, summary?, failures?}` |
| `token` | `{text}` — streamed prose for ask/clarify/modify |
| `plan` | `{plan: TripPlan, diff: []}` |
| `clarify` | `{question, slots, labels, options, reason_code}` |
| `error` | `{code, message, retryable}` |
| `trace` | `{request_id, spans, generations, tool_calls}` |
| `done` | `{request_id, intent, session_id}` |

A blocked injection returns `route` with `blocked: true`, a `token` safe reply and
`done`; no planning runs. A schema failure after bounded repair returns `error` with
`code: plan_invalid`, never a dirty plan.

### curl

```bash
curl -N -X POST http://127.0.0.1:8100/api/turn/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_x","message":"What is the weather on day 2?","locale":"en"}'
```

## 4. Feedback and memory

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/journeys/{id}/rating` | `{useful, stars?, reason_code?, client_token?}` | `{feedback, preferences_added}` |
| DELETE | `/api/journeys/{id}` | — | deletes turns, itineraries, state and the journey (Scrap plan) |
| DELETE | `/api/journeys/{id}/turns` | — | `{deleted, session_id}`; itinerary is kept |
| POST | `/api/journeys/{id}/share` | — | `{slug,url}`; public read-only redacted trip |
| GET | `/s/{slug}` | — | public HTML trip |
| GET | `/api/public/{slug}` | — | public JSON trip |
| GET | `/api/traveller?client_token=` | — | the caller's preferences, counts, destinations |
| PATCH | `/api/traveller` | `{client_token?, preferences}` | `{profile}` |
| POST | `/api/recall/clear` | `{client_token?}` | `{cleared, saved_trips, preferences}`: deletes only the caller's saved trips and preferences |
| GET | `/api/wishlist?limit=&client_token=` | — | the caller's saved-trip summaries |
| GET | `/api/popular?q=&limit=&client_token=&destination=` | — | cards from hybrid retrieval over useful-marked trips + trace (`mode`, `candidates`); public, `match` uses only the caller's own preferences |

**Caller identity** (traveller, recall/clear, wishlist, rating preferences, popular
matching): a valid `Authorization: Bearer` token is `user:<id>`; otherwise the guest
`client_token`. A `client_token` of `user:<id>` without that user's bearer is `401`, here
and on `POST /api/journeys` / `POST /api/turn/stream`. No identity at all is `401`, except
on `/api/popular`, which then ranks without personal matching. A useful rating saves the
trip for the journey's owner; planner recall reads only that owner's saved trips; signing
in moves the guest's saved trips and preferences to the account.

A `useful` feedback saves the plan in a background task with its embedding. The feedback
endpoint never blocks the response on the write. Trending prefers those **marked-useful**
plans; until one exists it falls back to anonymous workspace plans.

Clearing chat removes only `messages` for that session. The context and current plan stay,
so the traveller can keep working. Clearing memory removes the caller's saved trips and
preferences; the current chat/plan stay. Guest profile data is keyed by a random token in
localStorage; a signed-in profile is keyed by `user:<id>` and needs the bearer.

### Trending

```json
{
  "items": [
    {
      "destination": "Kyoto",
      "plans": 9,
      "days": 4,
      "year": 2026,
      "places": ["Kiyomizu-dera", "Fushimi Inari Taisha"],
      "summary": "4-day plan · 4 sights"
    }
  ],
  "trace": {
    "note": "Aggregated from anonymous plans created in this workspace...",
    "filter": "",
    "dropped": []
  }
}
```

Only destination, plan count, length, year and sight names are exposed. The endpoint never
returns a session id, client token, owner, budget, party size or generated summary.

## 5. Evaluation

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/api/eval/suites` | — | seeded and frozen |
| POST | `/api/eval/suites/{id}/lock` | header `X-Admin-Token` | freezes a custom dataset |
| GET | `/api/eval/trials` | `?limit=` | summaries (metrics, case counts, latency) |
| POST | `/api/eval/trials` | `{dataset_id, experiment_tag, split}` | returns the full run |
| GET | `/api/eval/trials/{id}` | — | full run with cases |
| GET | `/api/eval/trials/{id}/export` | — | Markdown report |
| GET | `/api/eval/contrast?a=&b=` | — | per-metric delta rows |

An unfrozen dataset returns `409`. An unknown dataset returns `404`. A planning run is
deterministic and sandboxed; see `docs/EVALUATION.md`.

## 6. Admin

| Method | Path | Header | Purpose |
|---|---|---|---|
| GET | `/api/admin/threats` | `X-Admin-Token` | aggregate injection counts |
| GET | `/api/admin/spans/{request_id}` | `X-Admin-Token` | one JSON trace |

The built-in default token is `local-admin`; `.env.example` ships the placeholder
`REPLACE_ME`. With `ENVIRONMENT` other than `development` or `test` (for example
`production`), the app refuses to start until `ADMIN_TOKEN` (neither of those two values)
and `AUTH_SECRET` (32+ characters) are set to real secrets. Trace ids must match `req_[0-9a-f]{12}`.

## 7. Contracts

- `contracts/openapi.json` — OpenAPI 3 document.
- `contracts/trip_plan.schema.json` — TripPlan JSON Schema.

Regenerate after a model change:

```bash
cd .
PYTHONPATH=backend python -m app.export_contracts
```

## 8. Error codes

| Code | Meaning | Retryable |
|---|---|---|
| `security_blocked` | blocklist hit | no |
| `canary_leak` | output contained the canary | no |
| `clarification_required` | missing slots (normally a `clarify` event) | yes |
| `plan_invalid` | validation failed after repair | yes |
| `provider_error` | data source failure (kind attached) | depends |
| `internal_error` | unexpected failure, no internals leaked to the user | yes |
