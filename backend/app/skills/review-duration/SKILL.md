---
name: review-duration
description: Use when a finished stop has no visit length in the table and the timetable must say whether that length is plausible.
---

# Review one visit length

You judge the length of a stop the table could not classify. You do not rewrite the day, invent a price, or ask for another search.

## When to use

A stop is listed with its name, its source types, and the minutes the timetable gave it. The traveller's message is included.

## Do not

- Do not change a length the traveller stated.
- Do not flag a museum, temple, or meal at the pace length.
- Do not invent a place or a time that was not in the timetable.

## Output

Return JSON only: {"verdict": "ok" or "too_short" or "too_long", "minimum_minutes": 90, "reason": "one sentence"}
