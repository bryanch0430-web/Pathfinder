---
name: read-the-stop
description: Use on every edit of a plan that already exists. The traveller's words name a stop already on that plan, including a wish or a nickname.
---

# Read which stop they mean

This is not the trip-start reader. Do not fill origin, dates, party size, or budget.

- Match their words to one stop already on the plan. A wish or a nickname is that stop: "sheep farm" is the farm stop, even when the official name is different. Set `target_item_id` to that stop's id and `place` to the words they used.
- "Another/other suggestion for a kind of place" is a request, never a question, even with a "?" ("ok other suggestion for dim sum restaruant?"). The stop of that kind on the plan is the target — match by what the stop IS, not its name: a dim sum restaurant, a ramen shop, an art museum. Set `operation` to `replace`, `target_item_id` to that stop, `place` to the kind spelled correctly ("dim sum restaurant", not their typo), and `is_question` to false. When no stop on the plan is that kind, `operation` is `add` and `target_item_id` stays null.
- Keep `operation` as `move`, `add`, `replace`, or `remove`. A day they name is `target_day`.
- A full day is not a reason to refuse. Still return the move. The application offers a tick to place the stop after the last one, and ticks that replace another stop. Nothing is written until they accept one.
- Do not invent a stop that is not on the plan.
