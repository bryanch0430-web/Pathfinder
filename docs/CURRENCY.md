# Currency handling and FX

## Chat statement

A message such as **“The currency I am using is TWD”** now:

1. is parsed into the `currency` slot;
2. is normalised and validated against the ISO-4217 set in
   `backend/app/core/currencies.py` (with aliases `NTD → TWD`, `RMB → CNY`);
3. updates `TripContext.currency`;
4. emits a `context` SSE event, so the trip bar and the budget/exchange widgets
   update without a page reload;
5. replies `Got it — I'll use TWD for this trip.`

A nonsense value (`NOPE`) does **not** change the stored context. The turn returns a
clarify card asking for a three-letter code and listing TWD, JPY, USD, HKD, EUR and GBP.

The same validation applies to the trip form (`PATCH /api/journeys/{id}/state` returns
400 for an invalid code) and to currency read from an uploaded itinerary.

The currency change is explicit and user-initiated; it is not inferred from a question
about exchange rates. If the message also contains a planning request, the plan proceeds
in the new currency and the context event arrives before the plan.

**Existing plan:** the current plan's cost is not silently relabelled. The context
currency is for the trip going forward; converting an existing plan's totals needs a
separate FX conversion pass.

## FX widget

`GET /api/wallet?base=HKD` uses `open.er-api.com` and returns up to the provider's full
rate table (currently 160+ currencies). If the provider is unavailable, the expanded
static fallback in `backend/app/api/misc.py` is used.

The trip form and the exchange widget use a searchable `datalist`:

- type a code (`TWD`) or a name (`Taiwan`) to filter;
- options show `TWD — New Taiwan dollar`;
- the exchange widget shows a rate only for a known code.

`GET /api/wallet?base=NOPE` now returns 404 instead of inventing a rate.

## Relevant code

- `backend/app/core/currencies.py` — ISO set, aliases, labels.
- `backend/app/understand/pipeline.py:merge_slots` — validates slot currency.
- `backend/app/turn/orchestrator.py` — currency statement, invalid-currency clarify, `context` event.
- `backend/app/api/journeys.py:patch_state` — form validation.
- `backend/app/api/misc.py:wallet` — provider/fallback and base validation.
- `frontend/src/lib/currency.ts` — searchable option list and labels.
- `frontend/src/design-system/molecules.tsx` — form and exchange pickers.
- `frontend/src/lib/usePathfinder.ts` — handles the `context` event.
- `backend/tests/test_api.py` — valid/alias/invalid currency tests.
