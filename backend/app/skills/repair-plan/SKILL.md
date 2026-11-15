---
name: repair-plan
description: Use when a trip-plan JSON document failed validation and only the named error may be corrected.
---

# Repair a plan document

Repair a trip-plan JSON document that failed validation. You receive the validation error and the invalid JSON document in the user message.

## When to use

A plan document has already been produced and a validator rejected it.

## Do not

- Fix only what the error names. Preserve every valid item, source, price, and note.
- Never add a fact that is not already in the document. Never invent a price, time, place, or source URL.
- Keep the same trip_id, session_id, and version.

## Steps

1. Read the validation error.
2. Change only the field the error names.
3. Return the complete corrected document.

## Output

Return the complete corrected JSON object only, no prose.
