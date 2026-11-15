---
name: attraction-search
description: Reference for how the attraction agent searches. Not a path the router chooses, and not injected into a writer prompt.
---

# Search for places

## When to use

The attraction agent owns places, coordinates, ratings, hours, and dining candidates. Python calls it while a plan is being built. This file records that search. It is not a traveller-facing path.

## Tools

`places.search`, `places.notes`, `geocode`.

## Steps

1. Google Places.
2. AMap for mainland China when `AMAP_KEY` is set.
3. Nominatim.
4. Wikivoyage.
5. If none answer, the section is unavailable.

## Do not

- A result the source did not return is not a stop.
- A named query keeps the source's own match, including when the traveller's spelling or language differs from the source's name.
