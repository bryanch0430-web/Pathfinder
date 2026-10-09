# Pathfinder web console

A Vue 3 + TypeScript + Vite + Pinia dashboard for the Pathfinder trip planner. It is deliberately
thin and **decoupled**: the only thing it shares with the backend is the HTTP/WebSocket API
contract (`../contracts/openapi.json`, plus the shared TripPlan JSON Schema in
`../contracts/trip_plan.schema.json`).

## Requirements

Node 18.19+ and npm 10 (developed on Node 18.19.0). No UI component library: plain CSS with
design tokens in `src/styles/tokens.css` (colours, radii, shadows, spacing, type) and a few shared
primitives in `src/styles/main.css`. Icons: `lucide-vue-next`. Font: Inter, bundled through
`@fontsource-variable/inter` so it works offline.

## Install and run

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

`npm run dev` expects the backend on port 8000. From the repo root, in another terminal:

```bash
uv run uvicorn backend.api.main:app --port 8000
```

The dev server proxies `/api` (HTTP and WebSocket) to `http://127.0.0.1:8000`, so the browser
only talks to one origin and no CORS setup is needed. The proxy target uses `127.0.0.1` rather
than `localhost` on purpose: on Node 18 `localhost` resolves to `::1` first, while uvicorn listens
on `127.0.0.1`, which fails with `ECONNREFUSED ::1:8000`. To point it elsewhere, set
`VITE_DEV_PROXY_TARGET` in `.env.local`.

The generated types are committed (`src/api/generated/openapi.ts`), so `npm run dev`,
`npm run typecheck` and `npm run build` all work without the backend running.

### Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server on :5173 with the `/api` proxy |
| `npm run build` | `vue-tsc --noEmit && vite build` (type-checks templates too, output in `dist/`) |
| `npm run typecheck` | `vue-tsc --noEmit` only |
| `npm run preview` | Serve the production build locally |
| `npm run gen:types` | Regenerate `src/api/generated/openapi.ts` from `../contracts/openapi.json` |
| `npm test` | Vitest component tests in jsdom (`src/components/__tests__/`), no backend needed |
| `npm run test:watch` | Vitest in watch mode |

### Configuration

Copy `.env.example` to `.env.local` if you need to change anything.

- `VITE_API_BASE`: base URL of the API. Empty (the default) means same origin, which is what the
  dev proxy gives you. If you set it to a separate origin, e.g. `http://localhost:8000`, the
  backend must allow that origin in its CORS settings. WebSocket URLs are derived from it
  (`ws://` or `wss://` to match).
- `VITE_DEV_PROXY_TARGET`: dev server only, where `/api` is proxied.

## Regenerating the types after a contract change

The backend writes the contract to `contracts/` (`backend/api/export_contracts.py`). After it
changes:

```bash
uv run python -m backend.api.export_contracts   # from the repo root
cd frontend
npm run gen:types
npm run build                                   # type errors show exactly what the change broke
```

`src/api/generated/openapi.ts` is generated: never edit it by hand. `src/api/types.ts` re-exports
friendly aliases (`TripPlan`, `TripContext`, `TurnResult`, `TurnEvent`, `SessionView`, ...) from
`components["schemas"]`.

## Layout

```
src/
  api/                 THE ONLY module that touches the network
    generated/         openapi.ts, generated, committed
    types.ts           aliases over the generated types
    client.ts          typed HTTP functions, openStream() for the WebSocket, ApiError
    index.ts           what the rest of the app imports ("@/api")
  stores/
    session.ts         conversation, route status, live turn progress, trip context, session lifecycle
    plan.ts            the TripPlan, selected day and stop, and the workspace selection (chat focus)
    ui.ts              active tab, open dialog
  components/          one file per component (below); tests in __tests__/
  lib/                 pure helpers: formatting, route status line, budget segments,
                       travel-time estimate, refresh chat messages, constants
  styles/tokens.css    design tokens
  styles/main.css      base styles + shared primitives (card, pill buttons, chips, badges)
  test/fixtures.ts     a typed TripPlan for tests
```

| Area | Components |
|---|---|
| Top bar | `TopBar` (logo, Plan / Saved trips tabs, search, help, avatar) |
| Left column | `LeftPanelSwitch` ("Your trip" / "Chat", one shown at a time) over `TripInputPanel` → `TripForm` (Generate); `ChatPanel` → `QuickChips`, `ChatMessage`, `ClarificationCard`, focus chip |
| Centre column | `PlanWorkspace` → `WorkspaceDay` → `WorkspaceStop` → `StopEditor`; `WorkspaceBookings` (hotel, tickets); `WorkspaceDetails` (cost, checks, reservations, disruptions, data sections) |
| Right column | `MapView` → `MapControls`; `PlaceDetailCard` → `PopularTimesChart` (hidden: no data), `StalenessBadge`; `BudgetCard` → `DonutChart` |
| Other tabs | `SavedTripsList` (placeholder) |
| Dialogs | `AppDialog` (modal shell), `RatingDialog` (Mark as useful, 1–5 stars), `TripForm` (trip details) |

Breakpoints: three columns at ≥ 1280 px, two at ≥ 900 px, one below (`App.vue`).

Rules that keep it decoupled:

- Components never call `fetch` or `WebSocket`. They read the Pinia store and call its actions.
- The store imports only from `@/api`, never from the generated file directly.
- Everything shown comes from the API types; there is no second copy of the schema.

A turn prefers the WebSocket stream (`/api/sessions/{id}/stream`), which gives the live route and
per-agent progress. If the socket cannot be opened, the same message is sent once through
`POST /api/sessions/{id}/messages` instead (no live progress). If the socket drops after the
server has started a turn, the message is not resent (the turn may have run); the session is
reloaded instead.

The session id is kept in `localStorage` (`pathfinder.sessionId`) so a reload resumes the same
session. If the server no longer knows it (the in-memory backend was restarted), a new session is
created.

## Swapping the framework

To replace Vue (React, Svelte, ...), keep two things and rewrite the rest:

1. `../contracts/` (the OpenAPI schema and TripPlan JSON Schema), the real interface.
2. `src/api/`: `client.ts` and `types.ts` are plain TypeScript with no Vue imports, and
   `npm run gen:types` works the same in any framework. Only `src/api/` plus the contracts are
   framework-independent.

`src/stores/`, `src/components/` and `src/App.vue` are Vue/Pinia specific. `src/lib/` is plain
TypeScript too, and mostly reusable, but it is not part of the decoupling guarantee.

## Notes and known limits

- `MapView` is a provider-agnostic placeholder (`TODO(provisional)`): an SVG with an
  equirectangular projection of the selected day's stops (numbered pins) and the hotel, with no
  basemap, so it works offline. Swap the `<svg>` for a real map behind the same inputs.
- Items the API does not expose (popular times, Food/Other costs, travel times, a saved-trips
  list, a refresh endpoint) are placeholders, derived values or chat messages. The
  full list is in `../DECISIONS.md` §12.
- The `/api/sessions/{id}/stream` WebSocket is not described by OpenAPI. Only the `TurnEvent`
  schema is exported, so the framing (client sends `{"message": "..."}`, server ends each turn with
  a `done` event carrying the `TurnResult`) is documented in `src/api/client.ts`.
