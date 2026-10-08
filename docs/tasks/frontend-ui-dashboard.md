# Task: Build the Pathfinder frontend UI (Vue 3 + TypeScript + Vite + Pinia)

## Context
The repo already contains the Pathfinder backend (FastAPI) and a Vue 3 frontend skeleton under
`frontend/`. Read `README.md`, `DECISIONS.md` and `backend/schemas/` (TripPlan JSON schema) first.
This task is frontend only. Do not change the backend API contract. If a UI element needs data the
API does not expose, render a placeholder and list the missing field in `DECISIONS.md`.
Everything must run with `npm run dev` against the backend's mock providers – no API keys, no DB.

## Visual direction (no images are attached – follow this description exactly)

Style: warm off-white page background (#F3F1EE); all content in white cards, radius 20px, 1px
#E8E5E0 border, very soft shadow. Accent: peach-orange (#F4A261) for active state, chart highlight
and selected-day ring. Primary buttons: solid black pill, white text. Secondary: white pill, thin
grey outline. Font: Inter or Manrope; headings medium weight; secondary text small and grey
(#8A8784). Thin icons (lucide-vue-next). Generous white space, no heavy dividers.
Must be responsive: three columns ≥1280px, two columns ≥900px, single column stack below.

## Layout – three-column dashboard

**Top bar (full width)**
- Left: Pathfinder logo mark + name.
- Centre: segmented pill tabs – "Plan", "Itinerary", "Saved trips" (the active tab is black pill).
- Right: circular icon buttons (search, help) and a user avatar circle.

**Left column – Chat panel + trip summary**
- Chat card: greeting "Hello! Where are we going?"; a row of small outlined quick-action chips
  (Hotel, Tickets, Attractions, Weather) that insert a starter sentence into the input; scrolling
  message list (user bubbles right, assistant left); streaming assistant text via the backend
  WebSocket; a text input with a send button. No voice input.
- Below each assistant message, a small grey status line showing the route taken
  ("Plan · 4 agents" / "Modify · hotel agent" / "Quick question" / "Needs clarification").
- When the router returns "unclear" or a missing-variable gate, show the clarification question
  as a highlighted card with inline inputs for the missing fields (dates, party size, budget) and a
  "Continue" button.
- Trip summary card (shown once a plan exists): title ("5 Days in Kyoto"), one-line subtitle, bullet
  list of what the plan covers (Accommodation, Attractions, Tickets, Weather), and a black CTA
  "Mark as useful" that opens a 1–5 star rating and posts to the save endpoint.

**Centre column – Map + selected place**
- Large map card with floating circular controls at the corners (zoom +, zoom −, layers, close).
  Map is a provider-agnostic placeholder component (`MapView.vue`) with a `TODO(provisional)`
  comment; it must render numbered pins for the selected day's stops from TripPlan coordinates
  using a simple SVG/canvas projection so it works offline.
- Place detail card beneath the map for the clicked stop: name, category label, an icon-row of key
  facts (price, opening hours, temperature for that day, travel time from previous stop), and a
  small "Popular times" bar chart with a weekday dropdown – only if the field exists in TripPlan;
  otherwise hide the chart.
- Each tool-sourced fact shows a tiny timestamp caption and, if stale or unavailable, a grey
  "unavailable – refresh" badge that triggers the modify path for that item only.

**Right column – Budget + Travel plan + Day timeline**
- "Budget Details" card: donut chart (total in centre, e.g. "Total 5-Day ¥48,000"), legend with
  Transport / Attractions / Food / Hotel / Other, and a percentage-used badge. Black pill button
  "Optimise my budget" sends a modify request ("reduce budget") through the chat.
- "Travel Plan" card: horizontal day selector – numbered circles, the active one ringed in the
  accent colour. Selecting a day updates the map, the timeline and the place card.
- "Day N" timeline card: vertical list of stops with time on the left, white pill on the right
  showing "Name · price"; a small lock icon on confirmed items (preserved across modify); drag
  handle to reorder, which calls the modify path with the new order.

## Components (one file each, `frontend/src/components/`)
TopBar, ChatPanel, ChatMessage, ClarificationCard, QuickChips, TripSummaryCard, MapView,
MapControls, PlaceDetailCard, PopularTimesChart, BudgetCard, DonutChart, DaySelector,
DayTimeline, TimelineItem, StalenessBadge, RatingDialog, SavedTripsList.

## State and API
- Pinia stores: `session` (conversation, route status), `plan` (TripPlan, selected day/stop),
  `ui` (active tab, dialogs).
- All HTTP/WebSocket access in `frontend/src/api/` only. Regenerate TS types from the backend
  OpenAPI/JSON Schema; no hand-written duplicates of TripPlan.
- Design tokens in `frontend/src/styles/tokens.css` (colours, radius, shadow, spacing).

## Constraints
- Do not add features absent from the proposal: no voice, no booking/payments, no model picker,
  no bot marketplace, no app download buttons.
- Keyboard accessible; visible focus ring; colour contrast ≥ 4.5:1 for text.
- Vitest component tests for ChatPanel routing status, ClarificationCard, DaySelector/Timeline
  sync, StalenessBadge refresh call, RatingDialog save call.
- Update `README.md` (frontend section with screenshots described in text) and `DECISIONS.md`.
