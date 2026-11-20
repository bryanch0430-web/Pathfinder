# Pathfinder LangGraph beta (16 Nov)

This folder keeps the LangGraph app's interface and adds the decision layer from the router version.

## What was different

The router version sends every message down one typed path: plan, modify, ask, or clarify. Jev (`jev-latest` on TypeSafe's systemone endpoint) is the decision model when a URL and key are set. A score under 0.8 asks a question instead of planning. A change reruns only the part it names and leaves the rest of the plan in place. Tools are allowlisted per path, so a question cannot search hotels or flights.

The LangGraph version plans through a graph and, on a follow-up, often rewrote the whole itinerary. Its studio already had a map and a calendar export, but the calendar was stops only, and the map had no day playback.

## What this beta adds

- System 1 router in `backend/app/routing/`: confident rules first, then Jev, then the writer model, then the rules. The 0.8 gate applies to every layer.
- Minimal edits: a follow-up stores only the days and kinds of stop it was allowed to touch. Moving a place removes it from the day it left.
- Tool allowlist for plan, modify, ask, and clarify. Hotel and flight lookups run only with `SERPAPI_KEY`. A question about a place already in the plan uses no tools.
- Calendar: studio **Calendar** button, and `GET /api/trips/{id}/calendar.ics`. Stays are all-day events. Route legs are included and marked as estimates. Event ids stay stable so a later import updates them.
- Map: on the studio Map tab, **Play** walks that day's stops and selects each one on the map.
- Each reply can show how it was routed (minimal change, question, Jev, rules) above the message.

Set `DECISION_API_URL` and `DECISION_API_KEY` to turn Jev on. With both empty, unambiguous messages are still decided by the rules and never call a model.

`frontend/node_modules` and `backend/.venv` are links to the LangGraph folder so this copy can run without a second install. Install inside this folder if you want it to stand alone.
