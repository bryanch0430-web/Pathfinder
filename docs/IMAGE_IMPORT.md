# Full itinerary-image import (v5)

v5 now reads a **day-by-day itinerary image or document** into a structured `ImportedItinerary`, then tries to build the trip from those named stops. Ordinary text and image messages that are not itineraries keep the existing single-city planner.

## Flow

1. The composer sends the image as a base64 `BufferedAttachment`.
2. `app/turn/orchestrator.py:_intake` sends the image to the vision model with `app/skills/intake.md`.
3. Intake may return `itinerary`:
   - `days`: each day's city/title and named stops;
   - `cities`: the cities in order;
   - `hotels`: named stays.
4. `_apply_intake` stores it on `TripContext.imported_itinerary`.
5. If the turn is a new plan, `_plan_turn` calls `app/plan/imported.py:build_imported_plan` **before** the normal gather:
   - every named stop is looked up through the same `Providers` used by the normal planner;
   - unsourced stops become gaps and are not invented;
   - the existing `build_plan`/`validate_structure` validates times, duplicates, cost and structure;
   - if too few stops are sourced, it clears the import and falls back to the normal single-city planner.
6. The imported plan is persisted through the same `_persist_plan`, SSE `plan` event, reply and trace as a normal plan.

## What it handles now

- Multi-city posters, e.g. Osaka → Kyoto → Tokyo.
- Day order from the image, with dates mapped from the trip's start date.
- Named attractions, restaurants and hotels; imported times when legible.
- Weather for each imported city where the provider answers.
- Duplicate stops across days: the first occurrence is kept; the normal builder rejects a repeated ref.
- Cross-city evidence: an Osaka plan will not use a Tokyo address just because the image mentioned Tokyo Disney.

## Deliberate limits

- **Hotels:** the existing cost model holds one stay per plan. The importer uses the first properly named/sourced hotel; it does not yet model a different hotel in each city.
- **Flights/tickets:** not imported from the image; the normal ticket provider is not run for an import. Travel legs are therefore empty.
- **Exact fidelity:** a named stop is kept only when a source lookup succeeds. The planner does not copy an unsourced name from the poster.
- **Image structure:** if the model cannot read a day/stop, it is omitted rather than guessed. The plan carries a `DataGap` for the missing source.
- **Normal planning is unchanged** when `imported_itinerary` is empty or the import falls back.

## Relevant code

- `app/skills/intake.md` — itinerary extraction instructions and JSON shape.
- `app/domain/models.py` — `ImportedStop`, `ImportedDay`, `ImportedItinerary`, `TripContext.imported_itinerary`.
- `app/plan/imported.py` — lookups, draft creation, fallback rule.
- `app/turn/orchestrator.py` — intake application, import branch, route override for "this image" requests.
- `tests/test_imported.py` — multi-day import and context round-trip.
- `tests/test_builder.py` — dedupe and fallback distribution.

## Reproduce the check

```powershell
cd backend
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe -m pytest -q tests
```

Then upload an itinerary image in the UI with dates and party size set. A full read produces a plan event with one day per imported day; an unreadable poster falls back to the ordinary planner with a visible reason.
