---
name: edit-itinerary
description: Use when the traveller wants to add, move, remove, or replace a stop or the hotel on the itinerary they already have, including "put it on day 3" and "change the hotel to the one I named".
---

# Edit the itinerary they already have

Read the current plan and the earlier turns before you decide. "It", "that", and "the hotel" mean something already said or already on the plan. A day number is a day, never the name of a place.

Add these three fields to the JSON you return:

- `operation`: `add`, `move`, `replace`, or `remove`.
- `place`: the place or hotel they mean, in their words. Empty when they named none.
- `target_day`: the day the stop should end on, counting from 1, or null. In "from day 1 to day 3" that is 3, not 1.

`move` keeps a stop that is already on the plan and only changes its day. `replace` names the new hotel or the new stop. Do not invent a place that neither this message nor the earlier turns named. A home is not a stop.

A name may be misspelled or written in another language. Put it in `place` as the traveller wrote it, including the characters they used. The place search resolves that name. A web page can confirm what a place is, but a page is not a stop: do not add a place that the place, hotel, or flight source did not return.
