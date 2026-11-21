---
name: rewrite-day
description: Use when a modify turn left the itinerary unchanged and the message is an open request about one or more days (pace, quieter day, drop a far stop) rather than a single add, move, replace or remove verb.
---

# Rewrite only the days the request names

Rebuild the named days from a closed list of candidate ids. You are still the planner: every stop must be an id from the payload. Days you do not list stay exactly as they are.

## When to use

The verb path already ran and changed nothing. The traveller asked for a shape of day (quieter, lighter, keep lunch and drop the far sight, start after a late arrival) rather than a single named swap.

## Do not

- Use only candidate ids that appear in `allowed_ids`. Never invent a place, a hotel, a flight, a price, or an opening hour.
- Do not touch a locked or confirmed stop: keep its id on the same day, in a sensible slot.
- Do not rewrite a day that is not in `touch_days`.
- Do not claim a reservation or a booking.

## Steps

1. Read the request, the outline of the whole trip, and the full detail of each day in `touch_days`.
2. For each touched day, return ordered attraction ids from `allowed_ids`, optional dwell minutes, where lunch sits, a short title, and one why sentence.
3. A stop that leaves a touched day may move onto another touched day. It must not vanish unless the request drops it, and it must not appear on an untouched day.

## Output

Return JSON only:

{
  "summary": "Two to four plain sentences that name what changed and what stayed.",
  "days": [
    {
      "day": 2,
      "item_ids": ["p1", "p3"],
      "dwell_minutes": [90, 120],
      "lunch_after": 0,
      "title": "A quieter afternoon in Gion",
      "why": "The late arrival leaves only the afternoon, so the far temple moves off this day."
    }
  ]
}
