# API

v5 serves **the existing Pathfinder wire contract**. The React app is carried over, so
the fastest correctness check is: every `request(...)` path in
`frontend/src/lib/api.ts` has a route here. That check is reproducible:

```powershell
cd backend
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe - <<'PY'
from app.api.app import create_app
import re
paths = create_app().openapi()["paths"]
print(len(paths), "documented routes")
PY
```

At the time of writing: 70 distinct frontend request paths, all covered; 86 routes in
the OpenAPI document (the extra ones are operator/eval helpers).

## Identity

| Header | Used for |
|---|---|
| `X-Client-Token` | a guest trip's owner token; also the identity for traveller memory |
| `Authorization: Bearer <token>` | a signed-in account (`/api/account/*`) |
| `X-Admin-Token` | operator routes (desk, eval, prompts, keys) |

Local requests (`127.0.0.1` or `LOCAL_ADMIN_PROXY`) count as the operator when
`LOCAL_ADMIN=true`, which is what opens the Evaluation and Desk tabs in a local demo.

## Journeys and plans

| Method | Path | Notes |
|---|---|---|
| POST | `/api/journeys` | new trip + default context |
| GET | `/api/journeys/{id}` | `SessionState`: context, latest plan, versions, messages, busy |
| GET | `/api/journeys/{id}/held` | desk hold + `planner_busy` |
| PATCH | `/api/journeys/{id}/state` | the trip form |
| GET | `/api/journeys/{id}/revisions` | version list |
| GET | `/api/journeys/{id}/itinerary?version=` | one plan version |
| POST | `/api/journeys/{id}/itinerary/restore` | copy an old version forward |
| POST | `/api/journeys/{id}/layout` | direct-edit grid (move rows, drop rows) |
| POST | `/api/journeys/{id}/proposals/{message_id}` | accept/reject an edit tick |
| POST | `/api/journeys/{id}/plan/choose` | swap an alternative / hotel / flight |
| POST | `/api/journeys/{id}/lock` | lock a stay or leg |
| POST | `/api/journeys/{id}/booked` | mark a stay or leg booked |
| GET | `/api/journeys/{id}/plan/hotels/{hotel_id}/rooms` | room types the plan holds |
| POST | `/api/journeys/{id}/finalise` | lock the trip, save it to the wishlist |
| POST | `/api/journeys/{id}/rating` | feedback |
| POST/DELETE | `/api/journeys/{id}/share` | share link |
| GET | `/api/public/{slug}`, `/s/{slug}` | public JSON / HTML |
| GET | `/api/journeys/{id}/translation?version=&locale=` | model translation, cached |
| GET | `/api/journeys/{id}/segments?day=` | map legs + anchors |
| GET | `/api/journeys/{id}/leg-options?from_id=&to_id=` | four modes between two stops |
| GET | `/api/journeys/{id}/geometry` | per-day points |
| GET | `/api/journeys/{id}/calendar.ics` | calendar export |
| POST | `/api/journeys/import` | open a plan file as a new trip |
| DELETE | `/api/journeys/{id}` / `/turns` | discard a trip / clear the chat |

## The turn stream

`POST /api/turn/stream` takes `{session_id, message, locale, scope_item_id,
scope_label, review, attachments?}` and returns `text/event-stream`. Frames:

```
event: progress   {phase, message, progress}
event: intake     {sources, filled, places}
event: route      {intent, confidence, threshold, need_clarification, reason_code,
                   affected_parts, missing_slots, related_to_plan, blocked}
event: agent      {agent, phase: start|end, status, elapsed_ms, summary}
event: plan       {plan, diff}
event: proposal   {message_id, proposal: {changes: [...]}, log: {steps: [...]}}
event: token      {text}
event: clarify    {question, slots, labels, options, choices, suggestions, reason_code}
event: think      {purpose, latency_ms, reasoning, output_preview}
event: tool       {name, status, latency_ms, note, args}
event: trace      RunTrace
event: done       {request_id, intent, session_id, plan_version?}
event: error      {code, message, detail?, retryable?}
```

Keepalives are SSE comments (`: keepalive`), never an event, so the UI parser is
untouched. One turn per trip is enforced with a seat; a second message waits.

## Evidence, guides, money

| Method | Path |
|---|---|
| GET | `/api/places/suggest?q=` |
| GET | `/api/images/place?name=` (redirects to a Wikimedia thumbnail) |
| GET | `/api/handbook?destination=&q=&limit=` |
| GET | `/api/wallet?base=`, `/api/wallet/convert` |
| GET | `/api/popular?q=&destination=` |

## Accounts and memory

`/api/account/signup`, `/signin`, `/whoami`, `/attach`; `/api/traveller` (GET/PATCH);
`/api/recall/clear`; `/api/wishlist` list/get/open.

## Models

`/api/llm/providers`, `/api/llm/keys` (list/save/test/patch/delete), `/api/llm/choice`.
Keys are stored in the local SQLite file and only a masked tail is returned.

## Operator

- `/api/runtime` (GET public shape), `/api/admin/runtime` (PUT/DELETE overrides),
  `/api/admin/redaction/preview`, `/api/admin/prompts` (list/save/reset),
  `/api/admin/escalation`, `/api/admin/threats`, `/api/admin/serpapi-keys`.
- Desk: `/api/admin/desk`, `/api/admin/desk/{id}` plus `hold`, `release`, `note`,
  `say`, `run`, `brief`, `plan`, `revisions`, `undo`, `layout`, `edit`, `itinerary`.
- Review: `/api/admin/cases`, `/c`,`/cases/turns`, `/cases/scan`, `/cases/check`,
  `/cases/suggestions/{id}/accept|dismiss|restore`.

## Evaluation

`/api/eval/suites`, `/suites/{id}/cases`, `/suites/{id}/lock`, `/reference`,
`/trials` (GET/POST), `/trials/{id}`, `/trials/{id}/export`, `/contrast`.

A trial runs synchronously and returns the finished run; the UI renders it directly.
Run times are in `docs/EVALUATION.md`.
