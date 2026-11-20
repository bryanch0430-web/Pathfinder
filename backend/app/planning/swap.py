"""Replace one stop on the studio plan.

A revise turn used to ask the model to reprint the whole itinerary. The reply
described a swap and the day list stayed the same. This writes the new place
onto that one stop, from a named request or from a Google Places listing.
"""

from __future__ import annotations

import re

_SWAP = re.compile(r"\b(swap|switch|replace)\b", re.I)
_CHANGE = re.compile(r"\b(change|another|different)\b", re.I)
_PLACE = re.compile(r"\b(restaurant|food|sushi|cafe|hotel|stay|guesthouse|place|option|meal|sight|museum)\b", re.I)
_TIME = re.compile(r"\b(morning|afternoon|evening|earlier|later)\b", re.I)
_TO = re.compile(r"\b(?:swap|switch|change|replace)\b.{0,80}?\b(?:to|with|for)\s+(.+)$", re.I)
_NOT_A_NAME = re.compile(
    r"\b(another|something other|other than|other option|different|something else|what you|you proposed|you suggested|the original|what it was|it back)\b",
    re.I,
)
_RESTORE = re.compile(
    r"\b(?:swap|switch|change|put)\b.{0,40}\bback\b|\bundo\b|\bwhat you (?:proposed|suggested|had|recommended)\b|\bthe original\b|\bwhat it was\b",
    re.I,
)
_SWAPPED = re.compile(r"Swapped (.+?) for (.+?) on day (\d+)", re.I)
_IT = re.compile(r"\b(it|another|different|other|something)\b", re.I)
_FOOD = re.compile(r"\b(lunch|dinner|breakfast|restaurant|sushi|cafe|food)\b", re.I)
_AVOID = re.compile(r"other than\s+([A-Za-z]+)", re.I)


def wants_restore(message: str) -> bool:
    """The traveler wants the place from before the last swap, not a new search."""
    return bool(_RESTORE.search(message or ""))


def wants_swap(message: str) -> bool:
    text = message or ""
    if _TIME.search(text) and not _PLACE.search(text):
        return False
    if _SWAP.search(text):
        return True
    return bool(_CHANGE.search(text) and _PLACE.search(text))


def named_target(message: str) -> str | None:
    match = _TO.search(message or "")
    if not match:
        return None
    name = match.group(1).strip(" .?!")
    if not name or _NOT_A_NAME.search(name):
        return None
    return name


def brief_hours(hours: object) -> str | None:
    """One readable line. Seven copies of the same day collapse to "Every day"."""
    if isinstance(hours, list):
        text = "; ".join(str(part) for part in hours if part)
    elif hours:
        text = str(hours)
    else:
        return None
    rows = []
    for part in text.split(";"):
        day, sep, span = part.strip().partition(":")
        if sep and span.strip():
            rows.append((day.strip(), span.strip()))
    if not rows:
        return text if len(text) <= 72 else text[:69].rsplit(" ", 1)[0] + "…"
    spans = {span for _, span in rows}
    if len(spans) == 1:
        span = next(iter(spans))
        if len(rows) >= 7:
            return f"Every day {span}"
        if len(rows) == 1:
            return f"{rows[0][0][:3]} {span}"
        return f"{rows[0][0][:3]}–{rows[-1][0][:3]} {span}"
    shown = [f"{day[:3]} {span}" for day, span in rows[:2]]
    return " · ".join(shown) + (" · later days differ" if len(rows) > 2 else "")


def _name_agrees(asked: str, found: str) -> bool:
    words = {word for word in re.findall(r"[a-z0-9]{4,}", asked.casefold())}
    haystack = found.casefold()
    return any(word in haystack for word in words)


def avoid_word(message: str) -> str | None:
    match = _AVOID.search(message or "")
    return match.group(1).casefold() if match else None


def _names(days: list[dict]) -> list[str]:
    return [str(item.get("name") or "") for day in days or [] for item in day.get("items") or []]


def _locate(days: list[dict], name: str, skip: str) -> tuple[int, int] | None:
    folded = (name or "").casefold()
    if not folded:
        return None
    for day_index, day in enumerate(days or []):
        for item_index, item in enumerate(day.get("items") or []):
            title = str(item.get("name") or "")
            if skip and title.casefold() == skip.casefold():
                continue
            if folded in title.casefold() or title.casefold() in folded:
                return day_index, item_index
    return None


def _foodish(item: dict) -> bool:
    category = str(item.get("category") or "")
    return category in {"food", "nightlife", "meal"} or bool(_FOOD.search(str(item.get("name") or "")))


def find_target(days: list[dict], message: str, selected: str | None, context: str | None) -> tuple[int, int] | None:
    """The stop to replace: the pin, a name in the message, or the place the last reply was about."""
    named = named_target(message) or ""
    if selected and (hit := _locate(days, selected, named)):
        return hit
    for name in sorted(_names(days), key=len, reverse=True):
        if len(name) > 3 and name.casefold() in (message or "").casefold() and name.casefold() != named.casefold():
            if hit := _locate(days, name, named):
                return hit
    if not _IT.search(message or ""):
        return None
    blob = context or ""
    best, at_best = "", 10**9
    for name in _names(days):
        if len(name) < 4 or name.casefold() == named.casefold():
            continue
        at = blob.casefold().find(name.casefold())
        if at >= 0 and at < at_best:
            best, at_best = name, at
    if best and (hit := _locate(days, best, named)):
        return hit
    for day_index, day in reversed(list(enumerate(days or []))):
        items = day.get("items") or []
        for item_index in range(len(items) - 1, -1, -1):
            title = str(items[item_index].get("name") or "")
            if _foodish(items[item_index]) and title.casefold() != named.casefold():
                return day_index, item_index
    return None


def pick_other(rows: list[dict], *, current: str, avoid: str | None, used: set[str]) -> dict | None:
    current_fold = (current or "").casefold()
    used_fold = {name.casefold() for name in used}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        blob = f"{name} {row.get('blurb') or ''} {row.get('cuisine') or ''}".casefold()
        if name.casefold() == current_fold or name.casefold() in used_fold:
            continue
        if avoid and avoid in blob:
            continue
        return row
    return None


def apply_swap(days: list[dict], day_index: int, item_index: int, listing: dict) -> list[dict]:
    copied = []
    for index, day in enumerate(days):
        items = []
        for inner, item in enumerate(day.get("items") or []):
            if index == day_index and inner == item_index:
                blurb = str(listing.get("blurb") or "").split(" Hours:")[0].strip()
                items.append({
                    **item,
                    "name": listing["name"],
                    "source_url": listing.get("source_url") or None,
                    "tip": blurb or None,
                    "hours": brief_hours(listing.get("hours")),
                })
            else:
                items.append(dict(item))
        copied.append({**day, "items": items})
    return copied


def _earlier_item(previous: list[dict] | None, name: str) -> dict | None:
    folded = name.casefold()
    for day in previous or []:
        for item in day.get("items") or []:
            if str(item.get("name") or "").casefold() == folded:
                return item
    return None


def _find_named(days: list[dict], name: str, day_number: int | None) -> tuple[int, int] | None:
    for day_index, day in enumerate(days or []):
        if day_number is not None and day.get("day") != day_number:
            continue
        for item_index, item in enumerate(day.get("items") or []):
            if name.casefold() in str(item.get("name") or "").casefold():
                return day_index, item_index
    if day_number is not None:
        return _find_named(days, name, None)
    return None


def restore_swap(days: list[dict], previous: list[dict] | None, context: str | None) -> dict | None:
    """Put back the place the last swap removed. A pinned stop does not choose the slot."""
    chosen = None
    for found in _SWAPPED.finditer(context or ""):
        removed_name, current_name, day_number = found.group(1).strip(), found.group(2).strip(), int(found.group(3))
        slot = _find_named(days, current_name, day_number)
        if slot is not None:
            chosen = (removed_name, slot)
    if chosen is None:
        return None
    removed, slot = chosen
    day_index, item_index = slot
    current = (days[day_index].get("items") or [])[item_index]
    earlier = _earlier_item(previous, removed) or {"name": removed, "tip": None, "hours": None, "source_url": None}
    restored = {
        **earlier,
        "start": current.get("start", earlier.get("start")),
        "end": current.get("end", earlier.get("end")),
        "category": current.get("category") or earlier.get("category"),
    }
    updated = []
    for index, day in enumerate(days):
        items = []
        for inner, item in enumerate(day.get("items") or []):
            items.append(dict(restored) if index == day_index and inner == item_index else dict(item))
        updated.append({**day, "items": items})
    text = f"Put {restored['name']} back on day {days[day_index].get('day')}, in place of {current.get('name')}."
    return {"text": text, "days": updated, "old": str(current.get("name") or ""), "new": str(restored["name"])}


def rename_stop(graph: dict, old: str, new: str) -> dict:
    """The map node that carried the old name now carries the new one."""
    if not isinstance(graph, dict) or not old or not new:
        return graph
    nodes = []
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and old.casefold() in str(node.get("name") or "").casefold():
            nodes.append({**node, "name": new})
        else:
            nodes.append(node)
    return {**graph, "nodes": nodes}


def _bucket(message: str) -> tuple[str, str, str]:
    if re.search(r"\b(hotel|stay|guesthouse)\b", message or "", re.I) and not re.search(r"restaurant|food|sushi", message or "", re.I):
        return "stay", "lodging", "sleep"
    if re.search(r"\b(sight|museum|temple)\b", message or "", re.I) and not re.search(r"restaurant|food|sushi", message or "", re.I):
        return "see", "sight", "see"
    return "food", "restaurant", "eat"


async def plan_swap(
    days: list[dict], message: str, selected: str | None, context: str | None,
    previous: list[dict] | None = None,
) -> dict | None:
    """The updated day list, or a note when no replacement listing came back."""
    if not wants_swap(message) or not days:
        return None
    if wants_restore(message) and named_target(message) is None:
        restored = restore_swap(days, previous, context)
        if restored:
            return restored
        return {
            "text": "I can put the last place back when I still have the swap that replaced it. Name the place, or use Restore previous plan.",
            "days": None,
        }
    target = find_target(days, message, selected, context)
    if target is None:
        return None
    day_index, item_index = target
    current = (days[day_index].get("items") or [])[item_index]
    old = str(current.get("name") or "that stop")
    city = str(days[day_index].get("base") or "")
    tab, kind, item_kind = _bucket(message)
    named = named_target(message)
    from app.services.place_search import as_around, search_places

    listing = None
    if named:
        rows = await search_places(named, kind=kind, limit=3, destination=city, fallback=False)
        if not rows:
            return {
                "text": f"I couldn't find {named} on Google Places, so I left {old} on the plan.",
                "days": None,
            }
        listing = as_around(rows[0], tab, item_kind)
        if not _name_agrees(named, str(listing.get("name") or "")):
            return {
                "text": f"I couldn't match {named} to a listed place. The closest result is {listing['name']}. Say that name if you want it.",
                "days": None,
            }
    else:
        avoid = avoid_word(message)
        query = f"best {kind if kind != 'sight' else 'tourist attraction'} in {city}" if city else kind
        if avoid:
            query = f"{query} not {avoid}"
        rows = await search_places(query, kind=kind, limit=8, destination=city, fallback=False)
        used = set(_names(days))
        listing = pick_other(
            [as_around(row, tab, item_kind) for row in rows], current=old, avoid=avoid, used=used,
        )
        if listing is None:
            where = city or "this stop"
            return {
                "text": f"I couldn't find another place in {where} to replace {old}. Pick one from Trip details, or name the place.",
                "days": None,
            }
    updated = apply_swap(days, day_index, item_index, listing)
    note = str(listing.get("blurb") or "").split(" Hours:")[0].strip()
    if len(note) > 140:
        note = note[:137].rsplit(" ", 1)[0] + "…"
    text = f"Swapped {old} for {listing['name']} on day {days[day_index].get('day')}."
    if note:
        text += f" {note}"
    return {"text": text, "days": updated, "old": old, "new": listing["name"]}
