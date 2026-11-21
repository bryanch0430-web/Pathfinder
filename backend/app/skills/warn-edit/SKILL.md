---
name: warn-edit
description: Use after an accepted add, to warn about distance, cost, or time while still keeping the stop.
---

# Warn, and still add the stop

The traveller already asked for this stop, and the edit JSON was accepted. The place search has already run. Add the stop the source returned. A full day, a long ride, or a high cost is a warning, not a reason to leave the plan unchanged.

Do not invent a coordinate, a price, or a source page. A name the search did not locate is not given a made-up pin.

## Limits

too_far_km: 25
day_end: 21:00

## Wording

Keep the Heads-up prefix. The reply shows these lines and does not store them on the plan.

too_far: Heads-up: {name} is about {km} km from the other stops on day {day}.
too_expensive: Heads-up: the trip is already over its budget, and {name} was still added.
time_overlap: Heads-up: day {day}'s usual times were taken, so {name} was placed at {start}–{end} instead of overlapping another stop.
day_runs_late: Heads-up: {name} ends at {end}, which is after {day_end}.
no_time: Heads-up: day {day} has no free minute left, so {name} was not added. Two stops cannot share the same clock time.
not_confirmed: Heads-up: the place search did not return a located {name}, so it was not added.
already: Heads-up: {name} is already on the plan, so it was not added again.
