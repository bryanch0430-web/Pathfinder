---
name: compose-itinerary
description: Use when the path is already plan. Compose the itinerary from the agents that were actually called.
---

# Compose from the agents that ran

The searches have already been chosen. A new plan does not call an agent it has nothing to ask:

- places, whenever a trip is being built
- the forecast, when the trip has dates
- a hotel, only when the dates include a night
- a flight, only when the traveller named a departure city

You receive only those results. Select and order their candidate ids. Do not invent a place, a hotel, a flight, or a price for an agent that did not run. If a section is absent, leave it out and say so in the summary.
