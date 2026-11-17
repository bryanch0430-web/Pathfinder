# PathFinder weekly update — 11 to 17 November 2026

Only my own commits (author `bryanch0430-web`); teammate and dependabot commits are left out.
Source: `git log --all --since=2026-11-10`, `docs/CHANGELOG.md` and `docs/EVAL_REGRESSIONS.md` on `origin/main` (`1651521`).

## At a glance

| | |
|---|---|
| Releases | **v1.7.0** (15 Nov) and **v1.8.0** (17 Nov), plus an "Unreleased" fix batch (17 Nov) |
| My commits on `origin/main` since 10 Nov | 122 |
| Backend tests | 20 failing → **0 failing**, 3,972 passed, 1 skipped |
| Evaluation | `hongkong` back to 8/8; token savings partly recovered (router 10.2% → 13.4%, edit 3.2% → 13.8%) |

## 1. Full-plan chat context (12 Nov)

- The whole plan (stay, transport, prices, ratings, choices, weather, tags, notes) can be written into the prompt and cut to fit without losing a day (`a425f49`, `216d915`).
- The day a message is about is worked out in one place: selected stop, day number or date (`88a0ce7`).
- An edit or question with nothing selected and no day named is read against the whole plan (`3d70a86`, `f5bf4fe`).
- A Claude model that refuses `temperature` is retried without it (`a07bdf8`).
- Merged `research-intake-confidence` (`b54d9c3`): stay research against room wishes; planning from a shared link, PDF or picture; a plan confidence score that triggers one more research round and asks about what stays open.

## 2. Multi-city trips (12–13 Nov, branch `multi-city-trips`, `fe07a79`)

- **Model:** a plan is one or more city segments; every hop is a leg with its own ranked flight and ground options (ground marked as estimates).
- **Planning:** cities are planned one by one with the existing pipeline, then stitched, costed, checked and assessed as one plan. Up to 8 cities, 30 days; the turn deadline and tool-call cap grow with the city count. Hotel and flight results are cached per session.
- **Route step:** works out nights per city, segments and the nearest city order.
- **Chat edits:** add, drop, move a city or change its nights; second hotel for a stay's last nights; edits are matched to the city they mean; a city visited twice is asked about.
- **UI:** trip form with up to 8 ordered cities and a one-way option; per-city sections; map follows the city of the day and can show all cities; Markdown, slides and version list group by city.
- **Richer options:** pictures with credits (Google, Wikimedia, hotel thumbnails); Book / Website / Map links; a rank badge with up to three code-written reasons.
- **Traveller profile:** pace, children, business, step-free access change stops per day, hours, ordering, stay ranking and transfers.

## 3. Choose and lock (14 Nov, branch `choose-and-lock`, `0e31e22`)

- Every stay, leg and airport transfer is a **choice slot**: options with **Lock in**, or the locked one with **Change**. A pill counts open choices and jumps to the first.
- Locks are kept by chat edits and route changes, which name any lock they release.
- Booked flights produce airport transfers, recomputed when the flight or stay changes.
- A leg under 300 km with no transit bus gets one estimated bus/shuttle option.
- A bare country or region for 4+ days asks which cities instead of planning.

## 4. v1.7.0 release (15 Nov)

Collected sections 2–3 into the release notes (`6acddff`). Fixed the image route rejecting every Google photo (name cap 300 → 2048, `bbfcecd`).

## 5. Agent-skill fixes and feature merges (16 Nov)

- Fixes I wrote on the `agent-skill` edit path, then merged (`3487ed4`): one-stop edits apply at once, multi-stop edits keep separate ticks, larger edits are held as one proposal; accepting prices every stay and refuses stale or locked offers; the direct-edit grid refuses overlapping or cross-city moves; plan files are cleaned on import; shared rate limit on hand-written plan versions.
- Nine feature/fix branches merged into `main` (`25170d3`):
  - `add-calendar-export` — download the plan as an `.ics` file in each city's time zone.
  - `add-booked-toggle` — mark a locked stay or leg as booked; booked items cannot be unlocked or dropped; exports list bookings.
  - `add-multicity-date-change` — new dates shift every city by the same offset and re-plan.
  - `add-best-order-from-origin` — "best order" starts from the city nearest the origin.
  - `ask-when-trip-too-large` — more than 8 cities or 30 days is asked about before planning.
  - `fix-budget-share`, `fix-ranking-factors`, `fix-expired-photo`, `fix-skill-review-items`.
- Also: nearest-airport lookup for cities not in the table (`c0b6eec`); weather questions per city on multi-city trips (`05b3471`).

## 6. Evaluation regression fixes (17 Nov)

Diagnosed every drop between 5 Nov and 16 Nov and written up in `docs/EVAL_REGRESSIONS.md` (`e4c6507`).

| Dataset | 5 Nov | 16 Nov | Now | State |
|---|---|---|---|---|
| `hongkong` | 8/8 | 7/8 | 8/8 | fixed |
| `injection` | 83/84 | 82/84 | 82/84 | open (unchecked model-written clarify question, `e40399d`) |
| router token saving | 36.3% | 10.2% | 13.4% | partly recovered |
| edit-vs-replan saving | 57.7% | 3.2% | 13.8% | partly recovered; every case cheaper than a replan again |
| `japan`, `routing`, `travelplanner` | 6/6, 64/84, 3/3 | same | same | — |

Fixes: "add more stops" works again; language/cuisine requests no longer trigger place searches; a late-fetched flight gets the arrival check and a note; shorter edit skill; `tzdata` added so the calendar export works on Windows; stale test scenes updated.

| Backend suite | Failed | Passed |
|---|---:|---:|
| 16 Nov (`25170d3`) | 20 | 3,948 |
| After first round (`88a4036`) | 7 | 3,961 |
| After second round (`cd28311`) | **0** | **3,972** |

## 7. v1.8.0 — UI/UX upgrade (17 Nov, `1651521`)

- Studio canvas: chat, plan and trip map as windows of one height; Reset layout.
- One Export menu: plan JSON, calendar, sketch, slides; sketchbook draws the current plan.
- "Why this decision" stays after reload (developer mode).
- Off-topic messages are answered in chat instead of becoming a question about the selected stop.

## Open items

- `injection` i09: the clarify question written by the model is sent unchecked.
- Token savings still below 5 Nov levels (edit prompt is 2.6× larger).
- Branch `final-report` is at `25170d3` and does not yet include sections 6–7.
- Local `main` is one commit behind `origin/main` (v1.8.0).
- After pulling: `uv pip install -r backend/requirements.txt` (new `tzdata`).
