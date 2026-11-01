# Changelog

All times are **HKT (UTC+8)**. Entries describe behaviour, not internals; the matching
commit is named for anyone who wants to read the diff.

---

## v1.2.0 — 2026-11-01 23:57

Clarity release: the console stops producing answers that *look* like actions but are not.

- **Identity questions answer instantly and honestly.** "Who are you?", "are you a real
  person", "which model are you" return a canned, testable reply in ~40 ms with
  `reason_code: identity` — no understand call, no router call, no web search. The reply
  never claims to be human. (`f1020c1`)
- **Clarify offers real next steps.** When the clarify is about intent rather than missing
  fields, the card lists four concrete messages (Plan my trip / Make it cheaper / What is on
  day 1? / What changed?). The Continue button no longer re-sends
  "Please continue with the current details.", which could only loop. (`dad3eaa`)
- **The source/freshness row reads as a label.** The whole `Live · google-places: …` strip
  sits on a subtle tinted band instead of looking like body text. (`f9725a4`, `3fd7672`)

## v1.1.0 — 2026-11-01 23:28

Ask release: questions the plan cannot answer are answered from real sources, and the
selected stop is actually used.

- **Web search, two sources with a policy.** DeepSeek Search (`/v1/tools/search`) answers
  interactive questions in ~0.6 s for $0.75/1,000 calls. Grok's Agent Tools search
  (`/responses` + `web_search`) synthesises across ~30 sources but takes 13–23 s, so it is
  opt-in (`GROK_SEARCH_ENABLED`) and reserved for synthesis questions. (`730ef5b`)
- **Follow-ups keep their thread.** "really?" / "why?" now receive the previous exchange, so
  the answer continues that topic instead of describing the whole itinerary. (`5ca9492`)
- **Opinion questions cite reviewers.** Google Places reviews are requested and three are
  passed to the writer, so "is it fun?" answers with what visitors praised, not just the
  editorial summary. (`a799184`)
- **The selected stop is honoured.** Place lookup and web search resolve the row selected in
  the console; a distance question with a stop selected is answered from that stop's
  coordinates. (`546c767`)
- **The question keeps its tag.** The sent message stores `meta.scope`
  ("Day 1 · Ocean Park"), rendered above the bubble and preserved across reloads.
  (`dec40a4`, `aa74cba`)
- **Distance, nearest and missing-section questions** are answered from data already held;
  a failed hotel or flight lookup now names the source error instead of "the plan has no
  hotel". (`e2fd42d`)

## v1.0.2 — 2026-11-01 22:50

Modify release: the path only reports changes it actually made.

- **"Make it cheaper" with nothing priced asks instead of pretending.** With no priced line
  in the plan it returns a question (replace the stop, or keep it), no version bump and no
  bogus gap. (`5ab24f7`)
- **Named swaps and duplicates.** "change Ocean Park to Disneyland" resolves the place after
  the last *to*, joined words resolve ("oceanpark" → Ocean Park), and a place already in the
  plan cannot be added twice: the reply names the existing day and time. (`c11f3f4`)
- **Meals are modifiable**, and a replacement can be targeted by index, name or scope.
  (`649a228`)
- **A no-op is not a version.** When the diff is empty the version stays put and the reply
  states the reason. (`649a228`)

## v1.0.1 — 2026-11-01 20:30

- **Product name is Pathfinder**, not "Pathfinder 30": README, HANDOFF, gap analysis, the
  FastAPI title, `app_name` and the regenerated OpenAPI contract.
- **Browser storage keys renamed** `pathfinder30.*` → `pathfinder.*` with a one-time
  migration, so existing sessions survive. (`eb7b9c7`)
- **Screenshots refreshed** for the current UI (14 of 18); see `docs/screenshots/README.md`.
  (`a4043c0`)

## v1.0.0 — 2026-11-01

First import of the project on branch `pathfinder-v1`: backend, frontend, evaluation
harness, contracts and documentation. (root commit)

---

## Known gaps at v1.2.0

Recorded here so the state of the system is not oversold:

- **Flights and hotels are unavailable** while the SerpApi account is out of searches
  (0 of 250). Plans show `0` priced lines and an explicit `data_error` gap; every
  budget-related measure is therefore provisional.
- **Estimates are not implemented.** The spec forbids presenting an estimate as a fare
  (`docs/SPEC.md`), so a missing price stays `unavailable`.
- **Rail is not connected**; tickets come from the flight source only.
