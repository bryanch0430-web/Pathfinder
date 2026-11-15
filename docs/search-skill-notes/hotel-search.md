---
name: hotel-search
description: Reference for how the hotel agent searches. Not a path the router chooses, and not injected into a writer prompt.
---

# Search for a stay

## When to use

The hotel agent owns stay candidates with price, rating, and distance to the centre. Python calls it only when the dates include a night. This file records that search. It is not a traveller-facing path.

## Tools

`hotels.search`.

## Steps

1. SerpApi Google Hotels.
2. Wikivoyage Sleep listings.
3. A named hotel is the search query.

## Do not

- A hotel the source did not return is not put on the plan.
- A one-day trip has no night, so this agent is not called.
