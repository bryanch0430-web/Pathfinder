# API

Base URL: `http://127.0.0.1:8100`. All bodies are JSON. Times are ISO 8601 UTC.

## 1. Meta

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ready` | process, writer, decision model, Langfuse, provider chains, thresholds |
| GET | `/api/runtime` | UI configuration, thresholds, freshness, provider chains, demo prompts |
| GET | `/api/handbook?destination=&q=&limit=&rerank=` | BM25+BGE hybrid by default; cited chunks with `last_verified`, `stale`, `content_hash`, `corpus_version`; `rerank=true` enables the optional BGE cross-encoder. Needs a Bearer token or an `X-Client-Token` header (401 otherwise) |
| GET | `/api/places/suggest?q=&limit=` | destination autocomplete (Places API New). Needs a Bearer token or an `X-Client-Token` header (401 otherwise) |
| GET | `/api/wallet?base=HKD` | cached live reference rates, source, date and note |
| GET | `/api/wallet/convert?amount=9000&from=HKD&to=USD` | converted amount and rate |
| GET | `/docs` | Swagger UI |

FX is keyless and cached for 12 hours. `open.er-api` is the live source; if it is
unreachable the dated static table is used and `source` becomes `static`, and it is not asked
again for a minute, so these public routes make at most one outbound call a minute while it is
down. The UI labels both and converts the known budget total into a chosen display currency.

`/api/ready` never proves every data key. A missing Places or SerpApi key appears as
a `data_quality.gap` on the plan, not a failed health check.

## 2. Auth and sessions

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/api/account/signup` | `{email,password,display_name?,client_token?,session_id?}` | `{token,user,profile_token}` |
| POST | `/api/account/signin` | same | `{token,user,profile_token}` |
| GET | `/api/account/whoami` | Bearer token | `{user,profile_token}` |
| POST | `/api/account/attach` | `{session_id, client_token}` — the guest token the session was created with | `{claimed}` |
| POST | `/api/journeys` | `{client_token}` | `{session, context}` |
| GET | `/api/journeys/{id}` | — | `{session, context, plan, versions, messages}` |
| PATCH | `/api/journeys/{id}/state` | `ContextPatch`: any `TripContext` field except `session_id`; `null` leaves a field unchanged | `{context}` |
| GET | `/api/journeys/{id}/itinerary` | `?version=` optional | `{plan}` |
| GET | `/api/journeys/{id}/revisions` | — | `{versions}` |
| GET | `/api/journeys/{id}/geometry` | — | map payload for the current plan |
| GET | `/api/journeys/{id}/segments` | `?day=YYYY-MM-DD` optional | `{trip_id, version, day, legs}` — real route geometry per leg |

Auth is optional. A guest session answers only to the guest token it was created with, sent as
the `X-Client-Token` header on every request. No session response carries the token; `GET` and
`PATCH /api/traveller` echo back the identity the caller presented, which is its own guest token
or `user:<id>`. Any other caller gets 404, not 403, and so does anyone for a row whose token is a
`user:` profile token that no account owns (an empty token never matches either). Once a session
is claimed by a user, requests without the matching Bearer token return 404 too. Sign-up and
sign-in move every guest session made with the guest token they present (`client_token`) to the
account, so the trips of the browser follow the traveller; `session_id` in the request is no
longer needed. `attach` moves one session and needs the token it was created with. A `user:`
token and an empty token move nothing. `PATCH` recomputes `days` from the date range (or
`end_date` from `days`). A `PATCH` made while a message is being understood is kept: the turn saves only
what the message changed, on top of the form as it is stored then.

A guest token is 1 to 120 printable ASCII characters without spaces (a UUID is fine). `POST /api/journeys`
and `POST /api/turn/stream` without a `session_id` take it from `client_token`, else from the
`X-Client-Token` header, and answer `422` for any other token, because the session could not be
opened again. A bearer needs no guest token. A request that would start a trip with neither is `401`.
The server does not issue guest tokens and cannot
tell a strong one from a weak one: whoever sends the same string opens the same trips, so use a
random value (the page uses a UUID), never `undefined` or a name. A trip made with a valid bearer
belongs to the account and is stored under its `user:<id>` token, whatever guest token the request names.

Opening a trip (`GET /api/journeys/{id}`, `GET .../itinerary` without `version`) fetches an outdated
forecast, fare or stay again and saves the result as the next version. While a message for the trip
is being worked on, the plan is returned as stored and nothing is saved, so that message cannot end in
`plan_conflict` because of a page that was opened; the turn refreshes an outdated plan itself first.

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

Messages for one trip run one after another. A message that waits for an earlier one gets
keepalive comments and no events; its deadline (`TURN_DEADLINE_SECONDS`, `turn_timeout` after that)
starts when its own turn does, so waiting never counts against it.

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
| DELETE | `/api/journeys/{id}` | — | deletes turns, itineraries, state and the journey (Scrap plan); a turn still running on it does not bring it back, what it saves after the delete is removed when it ends |
| DELETE | `/api/journeys/{id}/turns` | — | `{deleted, session_id}`; itinerary is kept |
| POST | `/api/journeys/{id}/share` | — | `{slug,url}`; public read-only redacted trip |
| GET | `/s/{slug}` | — | public HTML trip |
| GET | `/api/public/{slug}` | — | public JSON trip |
| GET | `/api/traveller` | — | the caller's identity (`client_token`), preferences, counts, destinations |
| PATCH | `/api/traveller` | `{client_token?, preferences}` | `{profile}` |
| POST | `/api/recall/clear` | `{client_token?}` | `{cleared, saved_trips, preferences}`: deletes only the caller's saved trips and preferences |
| GET | `/api/wishlist?limit=` | — | the caller's saved-trip summaries |
| GET | `/api/popular?q=&limit=&destination=` | — | cards from hybrid retrieval over useful-marked trips + trace (`mode`, `candidates`); public, `match` uses only the caller's own preferences |

**Caller identity** (traveller, recall/clear, wishlist, rating preferences, popular
matching): a valid `Authorization: Bearer` token is `user:<id>`; otherwise the guest
token, sent as the `X-Client-Token` header. A `client_token` query parameter or body field is
still read first, for older clients, but a URL is written to access logs: send the header. A
token of `user:<id>` without that user's bearer is `401`, here and on `POST /api/journeys` /
`POST /api/turn/stream`. No identity at all is `401`, except
on `/api/popular`, which then ranks without personal matching. A useful rating saves the
trip for the journey's owner, and the preferences a turn learns are remembered for that owner
too (a trip with no owner remembers none); planner recall reads only that owner's saved trips;
signing in moves the guest's saved trips and preferences to the account.

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

Every `/api/eval/*` route needs the `X-Admin-Token` header (401 otherwise). The web page sends the
token it finds in the browser's local storage under the key `pathfinder.admin` (and shows
"Admin token required" without it); nothing in the page sets it, see the README, section 6.

An unfrozen dataset returns `409`. An unknown dataset returns `404`. A planning run is
deterministic and sandboxed; see `docs/EVALUATION.md`.

## 6. Admin

| Method | Path | Header | Purpose |
|---|---|---|---|
| GET | `/api/admin/threats` | `X-Admin-Token` | aggregate injection counts |
| GET | `/api/admin/spans/{request_id}` | `X-Admin-Token` | one JSON trace |

The built-in default token is `local-admin`; `.env.example` ships the placeholder
`REPLACE_ME`. With `ENVIRONMENT` other than `development` or `test` (for example
`production`, which is also what an unset `ENVIRONMENT` means), the app refuses to start until `ADMIN_TOKEN` (neither of those two values)
and `AUTH_SECRET` (32+ characters) are set to real secrets. Trace ids must match `req_[0-9a-f]{12}`.

## 7. Contracts

- `contracts/openapi.json` — OpenAPI 3 document.
- `contracts/trip_plan.schema.json` — TripPlan JSON Schema.

Regenerate after a model or route change (a test fails while a committed contract is out of date):

```bash
cd .
PYTHONPATH=backend python -m app.export_contracts
```

It needs no `ENVIRONMENT` and no secrets, and takes an optional directory to write to instead of `contracts/`.

## 8. Error codes

| Code | Meaning | Retryable |
|---|---|---|
| `security_blocked` | blocklist hit | no |
| `canary_leak` | output contained the canary | no |
| `clarification_required` | missing slots (normally a `clarify` event) | yes |
| `plan_invalid` | validation failed after repair, or the destination could not be located | yes (no when the lookup cap refused the lookup) |
| `tool_budget_exceeded` | the turn reached the per-turn lookup cap (`MAX_TOOL_CALLS_PER_TURN`) | no |
| `plan_conflict` | another turn on the same trip saved its version first (turns on one trip otherwise run one after another) | yes |
| `provider_error` | data source failure (kind attached) | depends |
| `internal_error` | unexpected failure, no internals leaked to the user | yes |

## 9. Rate limits

Counted per process and per client address (the socket peer, or the client a trusted proxy
forwards; see the README) in a sliding one-minute window. A request over the limit is answered `429` with a `Retry-After` header
and is not counted. A limit of `0` turns it off.

| Setting | Default | Routes |
|---|---|---|
| `RATE_LIMIT_AUTH_PER_MINUTE` | 10 | `POST /api/account/signup` and `/signin` (one budget, counted before the password is checked) |
| `RATE_LIMIT_AI_PER_MINUTE` | 30 | `POST /api/turn/stream` and `POST /api/eval/trials` (one budget; a turn is counted once the trip is the caller's or a new one is allowed, and a run once the admin token is checked, so a `401`, `404` or `422` never uses it up) |
| `RATE_LIMIT_CREATE_PER_MINUTE` | 60 | `POST /api/journeys` and `POST /api/journeys/{id}/share` (one budget; a request that is refused with `401`, `404` or `422` makes nothing and is not counted) |
| `RATE_LIMIT_LOOKUP_PER_MINUTE` | 60 | `GET /api/places/suggest` and `GET /api/handbook` (one budget; the `X-Client-Token` they need is a string the caller makes up, so the budget follows the address and a new token does not reset it) |

The address is the only key. A caller can make up as many guest tokens as it likes, so no limit can
follow a token, and everyone behind one address (a NAT, or a proxy uvicorn is not told to trust)
shares its budgets: valid requests from one of them count against all of them.
