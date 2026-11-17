# Fix List for `agent-skill`, App Backlog, and Agent Workflow

Date: 2026-11-16. Written for agents (Opus / Sonnet) and for the two people on the project.

**Read first, every agent:** sections 0, 1, 2, 9 and 10 of `docs/superpowers/plans/2026-11-12-multi-city-handoff.md` (rules, commands, where things are, exit checklist, when to stop). Every rule there applies: test first; never edit an existing test to make it pass; existing backend `.py` files are changed by script, not the Edit tool (a format hook rewrites whole files); one plain-sentence commit per task with no attribution lines; never read `.env`; no push, merge or PR unless this document says a person approved it.

**State on 2026-11-16**
- `main` = `origin/main` = `6acddff` (v1.7.0: multi-city trips, choice slots and locks, airport transfers, links, ranks, pictures, traveller profile).
- `origin/agent-skill` = `f51fd48`, by the teammate (fungn): 5 commits ahead of `main`, 0 behind. Not merged.
- Baseline on `main`: 9 backend tests fail (listed in the handoff, rule 5). On `agent-skill` one of them (`test_edit_outcome_gaps`, "a stop that is already in the plan") is fixed, so its baseline is 8. Confirm by running the suite before changing anything.

---

## Part A — Fix list for `agent-skill` (must land before it merges to `main`)

What the branch adds: chat edits held as **proposals** the traveller accepts or rejects; a drag-and-retime **direct edit** grid; restore a version / import a plan file; a stop button; prompts moved to `backend/app/skills/<name>/SKILL.md`.

The problem in one line: proposals and direct edits only understand a one-city plan, and `main` now has cities, several stays, transport legs, locks and airport transfers.

Each fix: write the test first on the branch, see it fail, fix, see it pass.

### A1. A proposal must carry the whole edit when the edit is more than stops (must fix)

- **Where:** `backend/app/application/proposals.py` `proposal_changes` (line 85), `_stay_changes`, `_ticket_changes`, `_date_changes`.
- **Wrong today:** it compares stops, `first_stay`, the first ticket and dates. A change to segments, legs, a second or later stay, a chosen transport option, locks, party size or budget is not in any offered change, so accepting every offered change still loses it. (A probe edit that removed a stop, added a second hotel and changed party size kept only the removal.)
- **Fix:** add `def is_stops_only(old: TripPlan, new: TripPlan) -> bool` — true only when both plans have the same segments (ids, cities, dates), the same legs and the same selected option on each, the same chosen stays (id, dates, name), the same party size, budget and origin. `proposal_changes` offers its separate tick-or-cross changes only when `is_stops_only` is true. Otherwise it returns one change, `id="whole-edit"`, whose label is the edit's summary (reuse the text `diff_plans` already produces for the reply), whose detail is "Accepting applies every part of this edit; rejecting keeps your plan as it is.", and which carries the edited plan as the existing `rebuild` change does.
- **Tests (`backend/tests/test_proposals.py`):** an edit that adds a second hotel and changes party size → one `whole-edit` change, and accepting it stores both; a route change on the two-city sample plan (`tests/plan_samples.py::make_two_city_plan`) → `whole-edit`; choosing another option on a leg → `whole-edit`; a one-city stop removal → the separate changes it offers today (existing tests unchanged).

### A2. Accepting a proposal must be as careful as an edit (must fix)

- **Where:** `backend/app/interface/api.py` `decide_proposal` (about line 832).
- **Wrong today:** it prices with `planner._cost(context, updated.first_stay, ...)`, the one-city formula; it does not refresh airport transfers; a whole-plan proposal made against version N can be accepted after the plan moved to N+2 and overwrite later locks and choices; a bare `except Exception: pass` hides pricing failures; there is a leftover `ponytail:` comment.
- **Fix:**
  1. When a proposal is created (`orchestrator.py` about line 1104) store `base_version = plan.version` in the proposal. On accept of a `whole-edit`/`rebuild` change, refuse with 409 "The plan changed since this was offered; ask again." when the stored plan's version differs.
  2. Price with `planner.cost_for_plan(...)` (the function `main` uses for several stays; read its signature).
  3. After applying, run the same airport-transfer sync the choose route runs (`grep -n "sync_session_transfers" backend/app`).
  4. Before saving, check locks: every stay and leg that is locked in the stored plan must be unchanged in the plan being accepted; else 409 "This would change <name>, which is locked in. Press Change on it first."
  5. Replace the bare `except` with the specific exception the pricing can raise, and keep the old total only then. Remove the `ponytail:` comment.
- **Tests:** accept on a two-city plan gives the same `cost.total` as `cost_for_plan`; accept after an intervening lock → 409 and the plan unchanged; accept a change that would replace a locked stay → 409; a flight chosen in the accepted plan has its two transfer legs.

### A3. Nothing is saved until the traveller accepts (must fix)

- **Where:** `backend/app/application/orchestrator.py` about lines 1075-1130 (`sync_route` and `save_context` run before the hold).
- **Wrong today:** the trip form's cities are rewritten to match the edited plan even when the plan is only held; reject leaves the form saying one route and the plan another.
- **Fix:** when a turn ends in a held proposal, do not call `sync_route`/`save_context`. Store the context the edit would have saved inside the proposal (`"context": ...`), and save it in `decide_proposal` when a `whole-edit`/`rebuild` change is accepted.
- **Tests:** a held route-change proposal leaves `store.get_context(...).stops` as before; accepting it updates them; rejecting it leaves them.

### A4. Direct edit must keep the plan valid (must fix)

- **Where:** `backend/app/application/proposals.py` `apply_layout` (line 285); `backend/app/interface/api.py` the `/layout` route (about line 808); `backend/app/domain/models.py` (the deleted `DayPlan._no_overlap`).
- **Wrong today:** two stops at the same time can be saved; a stop can be dropped on a day that belongs to another city; transit and airport-transfer rows are not refreshed; the route takes no plan version and has no rate limit. To allow overlaps the branch deleted the model check that a day's stops do not overlap, which removes that guarantee for every plan from every path.
- **Fix:**
  1. Put `DayPlan._no_overlap` back exactly as on `main`.
  2. `apply_layout` refuses, with a `ValueError` the route turns into 422 and a sentence the page can show: an overlap ("<A> and <B> would overlap on <date>"); a move to a day in another segment ("<Stop> is in <City>; it can only move to a day in <City>"); any move or retime of a row of type `transit`, `ticket` or `hotel`; any move of a locked or confirmed stop (already refused).
  3. If the drag grid needs to hold an overlapping state while the traveller is still dragging, keep that state in the browser; only a valid layout is posted.
  4. After a valid layout: refresh transfers between stops with the function edits use (`refresh_transfers` in `application/transfers.py`), then the airport-transfer sync as in A2.
  5. The request carries `plan_version`; a mismatch is 409. Add the rate limit the choose route uses.
- **Tests:** overlap → 422 and nothing saved; cross-city move on the two-city plan → 422; moving a transit row → 422; a valid retime → new version, transfers refreshed; stale `plan_version` → 409; `DayPlan` with overlapping items raises again.

### A5. A new plan must always search hotels when it has dates (must fix; confirm first)

- **Where:** `backend/app/application/planning.py` `agents_for_new_plan` (about line 68).
- **Wrong today (reported, shape of real requests not traced):** it decides on `context.end_date`, so a form with a start date and a number of days but no end date gets attractions and weather only.
- **Fix:** decide on `context.date_range()` (which already combines start with end or days). Flights: keep "no origin → no flight search", that is intended.
- **Tests:** start + `days=4` → hotel agent included; start + end → included; no dates → as today.

### A6. New routes need the same protection as the old ones (should fix)

- `/layout`, `/proposals/{message_id}` and `/restore` get the rate limit `choose_option` has.
- Plan import (`api.py` about line 890): cap the upload at 1 MB (413 over it); clear every `locked` and `confirmed` flag on import; keep only `http`/`https` links (reuse the rule in `application/links.py`); regenerate links, ranks and airport transfers with `TripPlan.refresh_derived` and the transfer sync; refuse a file that does not validate as a `TripPlan` with 422.
- **Tests:** one per bullet; an imported plan with a `javascript:` link has none after import.

### A7. Check the reworded prompts did not change behaviour (should fix)

- `skills/clarify-trip/SKILL.md` now says "a stay that was not chosen is one such gap". With choice slots, an unlocked stay is normal. Test: a plan with every slot open and a complete trip form does not produce a clarifying question. If it does, remove that sentence.
- Run on the branch and report the numbers: `tests/test_proposal_efficiency.py` (edit must cost fewer tokens than a replan — TC-11), the single-city golden-plan test, the multi-city rig tests, and the prompt tests from step 1 (`test_plan_detail.py`, `test_plan_focus.py`, `test_modify_plan_context.py`, `test_ask_plan_context.py`).
- The edit skill's new fields (`operation`, `place`, `target_day`) are bypassed for multi-city plans (those go through `edit_multi_city`). Either pass them through to the per-city edit or say in the skill file that they apply to one-city plans only. Decide with the teammate; do not leave it silent.

### A8. Small items (nice to fix)

- Five skill files (`attraction-search`, `hotel-search`, `flight-search`, `weather-forecast`, `web-search`) are loaded by nothing. Either wire them or move them to `docs/` so nobody assumes they steer the agents.
- `skills.py` re-reads all 16 files on each cache miss; read once per process.
- `HANDOFF.md` and `README.md` say ten tests fail; the list has nine (eight on this branch).
- The frontend was never built on this branch by the reviewer: run `npm run typecheck`, `npm run build` and the node tests.

### Exit for Part A

The handoff's exit checklist (section 9) on `agent-skill`, plus: the multi-city, choice-lock, airport-transfer and plan-context test files all pass; `git diff main...HEAD -- backend/app/domain/models.py` no longer shows `_no_overlap` removed; the app check below.

App check (offline sources are fine): plan "Kyoto 2 nights then Osaka 2 nights"; lock the Osaka hotel; ask in chat to make all hotels cheaper → one whole-edit proposal that leaves the locked hotel alone; accept → totals right, lock kept; ask to remove a stop → separate tick-or-cross changes; open direct edit, try to drag an Osaka stop onto a Kyoto day → refused with the sentence; reject a route-change proposal → the form's cities unchanged.

---

## Part B — What the planning app still lacks (backlog, in order)

Each item is its own small branch from `main` after Part A merges, unless marked "independent".

| # | Gap | What to build | Size |
|---|---|---|---|
| B1 | Flights only for about 33 hard-coded cities (`providers/flights.py` `AIRPORTS`); Kanazawa, Hiroshima, Sapporo have none | Find the airport from the city's coordinates: bundle a public airport list (IATA code, coordinates, size), pick the nearest large or medium airport within 120 km, keep the table as an override. Independent. | M |
| B2 | Weather questions on a multi-city plan look up the first city only (`asking.py` about 630) | Use `segment_of(day)` for the day asked about; all cities when no day is named. Independent. | S |
| B3 | Changing trip dates by chat is refused on multi-city plans (`multi_edit.py` about 476) | Shift every segment by the same number of days, re-plan each city for its new dates, search legs again, release locks with the agreed sentence. | M |
| B4 | Budget: planning deducts transport first, edits do not; estimated ground travel is not costed | One function for a city's budget share used by both paths; cost notes say which legs are uncosted. | S |
| B5 | "Best order" only as a chat edit, starting from the first city | Start from the origin; offer it at planning time when the message asks for the best order. | S |
| B6 | An expired Google photo returns 502 (`api.py` about 566) | On a 404/expired name, look the place up again once and cache the new photo. After Part A (same file). | S |
| B7 | Ranking ignores review count, profile fit of alternatives, business amenities (`ranking.py`) | Add the three factors with fixed weights; tests on hand-made lists. | S |
| B8 | Over 30 days or 8 cities is refused at planning time | Ask which cities or days to keep, as understanding already does. | S |
| B9 | No calendar export | `.ics` export of days, stays and chosen transport, with each city's time zone. | M |
| B10 | Nothing can be marked as actually booked | A "booked" toggle on a locked stay or leg (`Reservation.status`), shown in exports. | M |
| Later | Real rail/bus/ferry fares and timetables; station transfers; visa and entry notes; time-zone shifts on estimated legs; frontend component tests | Each needs a data source or a decision first; do not start without one. | L |

---

## Part C — Agent workflow

### Roles

| Role | Model | Does | Never does |
|---|---|---|---|
| **Lead** (one session, the person's main chat) | most capable available | writes or updates the brief for each task, starts builders, reads their reports, starts reviewers, decides what goes to a person | write product code while a builder owns those files |
| **Builder** (one per task) | Opus for A1-A4 and B3; Sonnet for A5-A8 and the S-sized B items | follows one brief, test first, commits on its own branch, runs the exit checklist, reports | touch files outside its brief; push; merge; start other agents |
| **Reviewer** (fresh context, one per finished task) | most capable available | reads the diff against the brief, runs targeted tests in a temporary worktree, reports Critical / Important / Minor with file:line and whether each was run or reasoned | change the branch |
| **People** | Bryan (owner), fungn (teammate) | approve merges and pushes; decide the open questions below | — |

### Lanes and branches

Two lanes run at the same time. **One writer per set of files**: two builders never have the same file in their briefs.

- **Lane 1 — teammate branch.** `agent-skill-fixes`, cut from `origin/agent-skill`. Tasks A5 → A1 → A3 → A2 → A4 → A6 → A7 → A8, one builder at a time (they share `proposals.py`, `api.py`, `orchestrator.py`).
- **Lane 2 — backlog that does not touch Lane 1's files.** From `main`: `fix-airport-lookup` (B1: `providers/flights.py`, new data file, `transport_legs.py`), `fix-multi-city-weather` (B2: `asking.py`). These two can run in parallel with each other and with Lane 1.
- Everything else in Part B waits until Lane 1 is merged, because it touches `api.py`, `multi_edit.py` or `modification.py`.

### The loop for every task

1. **Brief.** The lead gives the builder: this document's item, the files it may touch, the tests to write, the exit checklist, and the stop conditions. Nothing else.
2. **Build.** Test first. One commit per test-and-fix pair.
3. **Self-check.** The builder runs the exit checklist and reports each line with the command and its last output line. A red line means the task is not done.
4. **Review.** A fresh reviewer gets the branch range and the brief, not the builder's opinion of its own work.
5. **Fix pass.** The builder fixes Critical and Important findings, each with a test that failed first. Minor findings are listed, not fixed. One fix pass; a second round of Critical findings goes to a person.
6. **Report to a person.** What changed, the checklist output, the reviewer's verdict, the decisions made, what is deferred.
7. **Merge, only on a person's yes.** Order: `agent-skill-fixes` → `agent-skill` (fungn approves) → `agent-skill` → `main` (Bryan approves) → Lane 2 branches rebased on the new `main`, re-checked, merged.

### Gates (a task cannot pass without these)

- Backend suite: only the known baseline failures for that branch.
- `ruff check`: not above the branch's starting count; new files clean.
- Contract regenerated when a model changed; `tests/test_export_contracts.py` passes.
- Frontend (when touched): `npm run typecheck`, `npm run build`, node tests.
- Single-city golden-plan test and TC-11 pass.
- No pre-existing file reformatted (`git diff --stat` shows only the task's lines).
- App check for anything a traveller can see.

### Stop and ask a person when

- A fix would change what the traveller sees in a way this document does not describe.
- TC-11 or the golden-plan test fails and the cause is the teammate's prompt rewording (A7): that is the teammate's call.
- A builder needs a file another builder owns.
- Anything needs a push, a merge, a key, a paid live search, or deleting something it did not create.

### Open questions for people (the lead does not decide these)

1. **Proposals on by default?** The web now sends every chat edit for review. Should a one-city stop swap still need a click to accept, or only larger edits?
2. **Direct edit across cities:** refuse (this plan) or allow and move the stop's city with it?
3. **Import:** keep it, given it lets a file put arbitrary content into a trip, or limit it to files this app exported?
4. **B1 airport data:** is bundling a public airport list acceptable, or should flights search by city name through the paid source?

### Kickoff prompts

Lane 1, first builder:

```text
Work in C:\Users\Bryan\Documents\PathFinder. Run `git fetch`, then create branch agent-skill-fixes from origin/agent-skill and work only there. Never push, merge or open a PR.
Read docs/superpowers/plans/2026-11-12-multi-city-handoff.md sections 0, 1, 2, 9, 10, then docs/superpowers/plans/2026-11-16-agent-skill-fixes-and-workflow.md Part A and Part C.
First run the full backend suite and report which tests fail before you change anything.
Then do fixes A5, A1, A3, A2, A4 in that order. For each: write the tests named in the fix, see them fail, make the fix, see them pass, commit with one plain sentence and no attribution lines. Existing backend .py files are changed by script, not the Edit tool.
After A4, run the exit checklist and the app check under "Exit for Part A" and report every line with the command and its last output line. Stop and report instead of continuing in the cases listed under "Stop and ask a person when".
```

Lane 2, airport lookup (B1) — only after question 4 is answered:

```text
Work in C:\Users\Bryan\Documents\PathFinder. Create branch fix-airport-lookup from main and work only there. Never push, merge or open a PR.
Read docs/superpowers/plans/2026-11-12-multi-city-handoff.md sections 0, 1, 2, 9, 10, then item B1 in docs/superpowers/plans/2026-11-16-agent-skill-fixes-and-workflow.md.
You may touch only backend/app/infrastructure/providers/flights.py, a new data file beside it, backend/app/application/transport_legs.py and new test files. Read resolve_airport and its callers first.
Test first: Kanazawa, Hiroshima and Sapporo each resolve to an airport; every city in the existing table resolves to the same code as today; a place with no airport within 120 km resolves to none; no network call is made.
Run the exit checklist and report every line with the command and its last output line.
```

Lane 2, weather (B2):

```text
Work in C:\Users\Bryan\Documents\PathFinder. Create branch fix-multi-city-weather from main and work only there. Never push, merge or open a PR.
Read docs/superpowers/plans/2026-11-12-multi-city-handoff.md sections 0, 1, 2, 9, 10, then item B2 in docs/superpowers/plans/2026-11-16-agent-skill-fixes-and-workflow.md.
You may touch only backend/app/application/asking.py (by script, not the Edit tool) and new test files. Read how the weather facts choose their place (around line 630 and its caller around line 200).
Test first, on tests/plan_samples.py make_two_city_plan(): a weather question about a day in the second city looks up the second city; a question naming no day covers both cities; a one-city plan behaves exactly as before.
Run the exit checklist and report every line with the command and its last output line.
```

Reviewer, any finished task:

```text
Read-only review in C:\Users\Bryan\Documents\PathFinder. Do not change the working tree, index, HEAD or any branch. For any test run, use a temporary git worktree outside the repo and remove it afterwards. Do not start other agents.
Review <branch> against <base> for the item <A-n or B-n> in docs/superpowers/plans/2026-11-16-agent-skill-fixes-and-workflow.md. Check: every test the item names exists and fails without the fix; the fix does what the item says and nothing else; multi-city plans, locks and airport transfers still work; no existing test was edited; no pre-existing file was reformatted; no route lost its session check or rate limit.
Report Critical / Important / Minor with file:line, and for each say whether you ran it or reasoned it. End with: merge / merge after fixes / do not merge.
```
