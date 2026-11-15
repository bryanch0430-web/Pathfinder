---
name: attraction-search
description: How the attraction agent searches. Loaded as a reference in this folder, not as a path the router chooses.
---

# Attraction agent

Owns places, coordinates, ratings, hours, and dining candidates. Tools: `places.search`, `places.notes`, `geocode`.

Search order: Google Places, then AMap for mainland China when `AMAP_KEY` is set, then Nominatim, then Wikivoyage. If none answer, the section is unavailable. A result the source did not return is not a stop. A named query keeps the source's own match, including when the traveller's spelling or language differs from the source's name.
