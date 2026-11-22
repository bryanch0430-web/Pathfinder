---
name: add-a-real-place
description: Use when a change asks to add a kind of place or a thing to see. It is a new stop, not a rename of the stop that is selected.
---

# Add a real place

An add puts a new stop on the day. The stop selected on the page is not the thing being replaced.

This file does not list sights. Run the script, then search for the phrase it returns. The first result that has a map point is the place, even when its name does not repeat the phrase.

```bash
python scripts/resolve.py "add a stop on day 1 where I can see the night view"
```

The script prints JSON: `add`, `remove`, `day`, and `query`. `query` is the words that are left after the day and the polite words are taken off. The application calls that function on every add, so a new way of asking does not need a new line in this file.

## See

The words after these are what to look up. The day is not part of the lookup.

Examples:
- where i can see
- where to see
- to see

## Like

The words after these are the kind of place.

Examples:
- like a
- like an
- such as

## Add

These words ask for a new stop, even when "attraction" is misspelled.

Examples:
- add
- include
- insert

## Same wish

A result is the wish when its own name or description uses any word on the line. The result must still have a map point.

Examples:
- sakura | cherry | blossom
- mall | shopping
- gundam | gundam
