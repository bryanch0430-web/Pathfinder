---
name: flight-search
description: Reference for how the ticket agent searches. Not a path the router chooses, and not injected into a writer prompt.
---

# Search for a flight

## When to use

The ticket agent owns flights and reference fares. It runs when the traveller has named a departure city. This file records that search. It is not a traveller-facing path.

## Tools

`flights.search` on SerpApi Google Flights. Rail is not connected.

## Steps

1. Search only when an origin is known.
2. Without an origin there is no flight to search.

## Do not

- A fare the source did not return is not added.
