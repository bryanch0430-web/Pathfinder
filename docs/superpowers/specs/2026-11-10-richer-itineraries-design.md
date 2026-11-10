# Richer itineraries: meal options, alternatives, hotel/flight choice, refine loop

Status: agreed with the owner 2026-11-10 (owner then asked to implement directly; items marked
*builder's call* were not discussed).

## Intent
Itineraries today give one lunch and one dinner per day from a city-wide pool of 6 restaurants
(long trips run out), show one hotel, treat the first flight as chosen, and are checked only
structurally. The owner wants several real choices the traveller picks in the UI, and a plan that is
checked and repaired against real-world constraints before it is shown.

Priority (owner): 1 meal options, 2 hotel options, 3 flight selection, 4 refine loop.

## Hard rules (owner)
- Never invent facts. Show reservation info only when the source says so. Dishes only when directly
  derived from source data (labelled "Mentioned in reviews"); v1 shows none (*builder's call*: no
  deterministic extractor yet, and an LLM extractor would add calls).
- Distances, travel and walking times are computed by the app, never by the model.
- Deterministic tools first; AI only for ranking/preference reasoning where it already runs.
- Codex = development helper / second reviewer, not a product dependency.
- `_refusal` and `_restore_facts` in validation.py are not loosened.

## Meals
- Slots per day: breakfast, lunch, dinner (2–3 options each: one default on the timeline + 1–2
  alternatives), plus optional cafe/snack stop(s) between attractions.
- Options are searched around each day's own stops after the writer call (Places text search with a
  location bias at the day's centroid), in parallel, with a time budget; on failure fall back to the
  existing city-wide pool. No extra LLM calls.
- Each restaurant shows rating, review count, price level/range, cuisine (from the source's place
  type), walking distance from the previous stop (computed), and `reservable` only if the source
  returned it.
- Attractions also carry up to one nearby alternative from the unused candidate pool.

## Plan model
- `PlanItem.alternatives: list[PlanAlternative]` (default `[]`): source-backed rows with the same
  facts as the item (name, geo, address, rating, review_count, price_level, cuisine, reservable,
  opening_hours, source_refs, booking_url) plus computed `distance_m` from the default.
- `PlanItem` gains optional `meal_slot` (breakfast|lunch|dinner|snack), rating, review_count,
  price_level, cuisine, reservable.
- `TripPlan.hotel_options: list[HotelStay]` (up to 5); `hotels[0]` stays the chosen stay.
  `HotelStay` gains amenities, hotel_class, room_types (filled only when the source gives them).
- `TripPlan.tickets` keeps up to 5 options; `TicketOption.selected` marks the chosen one (default:
  first). Cost and reservations use the selected one.
- All new fields default empty so stored plans still load.

## Choosing in the UI (no LLM, no chat)
- `POST /trips/{id}/plan/choose` with `{kind: "item"|"hotel"|"flight", target_id, option_id}`.
  Swaps deterministically, recomputes that day's legs/cost, runs the refine checks, saves a plan
  version. Kept out of modification.py (a fix there is in review).
- Room types: fetched on demand per hotel (SerpApi property details) only when the user opens it;
  cached on the plan.

## Refine loop (deterministic)
After compose and before validate/repair: evaluate → fix → re-evaluate, at most 3 rounds, no LLM.
Checks: closed on visit day / outside hours; travel time between consecutive stops vs gap; meal
outside its window or venue closed; day load (activity hours, walking km); budget over cap.
Fixes: reorder (existing 2-opt/open-path helpers), swap a closed default for an open alternative,
move a meal, drop the lowest-value stop on an overloaded day into alternatives. Remaining issues
become plan notes. The existing LLM repair (max 2) still runs only for structural errors.
Budget: the loop is pure CPU (ms). Meal searches cap at ~15 s wall-clock, inside the 180 s turn.

## Non-goals
Multi-destination trips (other session), deeper OpenAI integration, booking.
