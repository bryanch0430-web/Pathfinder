# Frontend

React 18 + TypeScript + Vite. The application is composed from **Storybook Pathfinder**;
pages do not invent spacing, colour or component variants.

## Commands

```bash
npm install
npm run dev              # http://localhost:5173, proxies /api to :8100
npm run typecheck        # tsc --noEmit
npm run build            # dist/ (the backend serves it)
npm run storybook        # http://localhost:6006
npm run build-storybook  # storybook-static/
```

`VITE_API_TARGET` overrides the dev proxy target. In production the backend serves
`dist/`, so the API base is the same origin.

The map uses Google Maps JS for every destination, with the key served from
`/api/runtime`. Pins and routes are drawn from verified coordinates. It uses numbered sight pins plus an offset `H` hotel marker. A 06:00–23:00 clock scrubber
and play button drive the pin states, route progress and a moving traveller token; the
token changes glyph/colour for walk, metro, drive and flight, and clicking a stop tick
jumps to that time and opens its place card. If tiles fail, the pins still render on the
paper background.

The `TrendingRail` under the map reads `/api/popular`, a workspace-local aggregate of
anonymous plans (destination, days, year, sight names only). `Try this trip` requires a
second confirmation before it sends anything to the assistant.

The trip form defaults to **HKD** and puts the currency select inside the Budget row.
After a plan exists, `BudgetPanel` shows the `/api/wallet` monitor: source/date plus the known
total converted into a selectable display currency.

Map playback has `1× / 2× / 4×` speed controls. The token is animated with
`requestAnimationFrame` between the previous and next stop, so it travels continuously
instead of jumping at the next item boundary.

The chat header has a trash button that clears only the message history. Settings contains
an anonymous profile (random browser token), remembered preferences, and **Clear memory**
for saved trips/preferences.

## Storybook first

1. `src/design-system/tokens.css` — primitive tokens + `--pf-*` semantic aliases.
2. `atoms.tsx` — one-job controls.
3. `elements.tsx` — status, intent, agent, source, empty, progress.
4. `molecules.tsx` — chat, form, clarify, itinerary, budget, evaluation cards.
5. `organisms.tsx` — header, chat panel, workspace, map, evaluation, settings.
6. `templates.tsx` — planner and page skeletons.
7. `stories/` — every tier has a playground plus state/variant matrices.
8. `src/app/` imports only `design-system` components.

Rules:

- A component may only use token values; no raw hex/px in component code.
- The application may not override a design-system class to change its look.
- If a screen needs a new visual, add it to the design system and a story first.
- Every interactive element is a native control with a visible focus ring.
- All screens implement empty, loading and error states.

## Application state

`src/lib/usePathfinder.ts` owns:

- session creation and persistence (`localStorage`);
- context form state and `PATCH /context`;
- POST + SSE stream parsing (`src/lib/api.ts`);
- optimistic user message, streamed assistant tokens;
- live agent chips, route/progress events;
- canonical refresh after `done` (server messages, versions, plan);
- item selection, version switching, feedback.
- what a failed call is told to the traveller (`src/lib/failure.ts`): one of the page's own messages, kept as a
  key so a language switch translates it, or the server's own `detail` sentence; never an error page or an
  exception's text.

`src/App.tsx` is the composition: header + `PlannerTemplate` + pages. The evaluation,
saved-trips and settings pages fetch their own read-only data.

## i18n

English is primary. `src/lib/i18n.ts` holds English, Simplified Chinese and Traditional
Chinese dictionaries for console chrome. Generated plan text stays English-first; place
names remain as the source returned them. Adding a locale is one dictionary entry.

## Verified

- `npm run typecheck` clean
- `npm run build` clean
- `npm run build-storybook` clean
- Live plan/ask/modify flow exercised with Playwright; no console or page errors

---

**Version note.** Tracks the repository release described in [CHANGELOG](../docs/CHANGELOG.md);
current release **v1.2.0 (2026-11-01 23:57 HKT)**.
