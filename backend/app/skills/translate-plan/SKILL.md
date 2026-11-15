---
name: translate-plan
description: Use when display text of an existing plan must be translated into Traditional Chinese for a Hong Kong traveller, with numbers, dates, and placeholders unchanged.
---

# Translate the plan for display

Translate the display text of a travel plan from English into Traditional Chinese for a traveller from Hong Kong. You translate; you never add, drop, explain, or plan.

## When to use

The plan is already composed and the display locale is `zh-Hant`.

## Input

A JSON object `{"locale": "zh-Hant", "glossary": {...}, "segments": [{"id": "...", "text": "..."}]}`. Each segment is one field of the plan (a summary, a day title or note, a stop note, a data-gap message).

## Steps

1. Write Traditional Chinese with Hong Kong wording and conventions (for example 的士, 巴士, 港鐵, 酒店, 資訊), not Taiwan or Mainland wording.
2. Use the glossary translation for every glossary term.
3. Copy unchanged, character for character: every number, time (such as 09:30), date (such as 2026-11-05), price and currency code (such as HKD 120), percentage, URL, id, and every placeholder in double braces (such as {{P1}}). Do not convert digits into Chinese numerals, do not reformat dates or times, and do not convert currencies.
4. Place names are given as placeholders; keep each placeholder exactly once where it belongs. Never translate, invent, or add a place name.
5. Keep the meaning and the level of certainty. If a segment is empty, return it empty.

## Do not

- Do not add, drop, explain, or plan.
- Do not convert digits into Chinese numerals, reformat dates or times, or convert currencies.
- Never translate, invent, or add a place name.

## Output

Return one JSON object only: `{"segments": [{"id": "...", "text": "..."}]}` with exactly the same segments, the same ids, in the same order, each `text` translated.
