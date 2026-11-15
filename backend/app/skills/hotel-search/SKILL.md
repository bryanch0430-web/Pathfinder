---
name: hotel-search
description: How the hotel agent searches. Loaded as a reference in this folder, not as a path the router chooses.
---

# Hotel agent

Owns stay candidates with price, rating, and distance to the centre. Tool: `hotels.search`.

Search order: SerpApi Google Hotels, then Wikivoyage Sleep listings. A named hotel is the search query. A hotel the source did not return is not put on the plan. A one-day trip has no night, so this agent is not called.
