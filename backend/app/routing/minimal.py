"""Minimal change: a revision may replace only the days and kinds of stop it was about.

The model still writes a full itinerary, because that is what the chat shows. The
stored day plan is then merged so an edit to day 2 cannot quietly rewrite day 1,
and a move removes the place from the day it left.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

ATTRACTIONS = {"sight", "food", "nature", "experience", "shopping", "nightlife", "other"}
PART_CATEGORIES = {
    "hotel": {"stay"},
    "ticket": {"transit"},
    "attraction": ATTRACTIONS,
}


def place_key(name: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(c)).casefold()
    plain = re.sub(r"\(.*?\)", "", plain)
    return re.sub(r"[^0-9a-z]+", "", plain)


def _day_numbers(parts: list[str]) -> set[int]:
    days: set[int] = set()
    for part in parts:
        if part.startswith("day_"):
            try:
                days.add(int(part.split("_", 1)[1]))
            except ValueError:
                continue
    return days


def _categories(parts: list[str]) -> set[str] | None:
    found: set[str] = set()
    for part in parts:
        found |= PART_CATEGORIES.get(part, set())
    if _day_numbers(parts) and not any(part in PART_CATEGORIES for part in parts):
        return None
    return found or None


def apply_minimal_change(
    before: list[dict] | None,
    after: list[dict] | None,
    before_graph: dict | None,
    after_graph: dict | None,
    parts: list[str],
    *,
    message: str = "",
) -> tuple[list[dict] | None, dict | None, list[dict]]:
    """Return the day plan and graph to store. Notes are empty: the receipt is the diff itself."""
    if not before:
        return after, after_graph, []
    if not after:
        return before, before_graph or after_graph, []
    if not parts:
        return before, before_graph or after_graph, []
    if set(parts) <= {"budget", "weather"}:
        return before, before_graph or after_graph, []

    if "dates" in parts:
        merged = _merge_length(before, after, _categories([part for part in parts if part != "dates"]))
    else:
        merged = _merge_scope(before, after, _day_numbers(parts), _categories(parts))
        merged = _drop_moved(merged, before, _day_numbers(parts), message)
    graph = _prune_graph(after_graph or before_graph, merged)
    return merged, graph, []


def _merge_scope(before: list[dict], after: list[dict], days: set[int], categories: set[str] | None) -> list[dict]:
    by_new = {day.get("day"): day for day in after if isinstance(day, dict)}
    merged = []
    for old in before:
        number = old.get("day")
        new = by_new.get(number)
        if days and number not in days:
            merged.append(old)
            continue
        if not isinstance(new, dict):
            merged.append(old)
            continue
        merged.append(_swap(old, new, categories) if categories else new)
    return merged


def _swap(old: dict, new: dict, categories: set[str]) -> dict:
    kept = [item for item in old.get("items") or [] if item.get("category") not in categories]
    incoming = [item for item in new.get("items") or [] if item.get("category") in categories]
    kept_keys = {place_key(item.get("name") or "") for item in kept}
    items: list[dict] = []
    seen: set[str] = set()
    for item in kept + [item for item in incoming if place_key(item.get("name") or "") not in kept_keys]:
        key = place_key(item.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(item)
    return {**old, "base": old.get("base") or new.get("base"), "items": items}


def _merge_length(before: list[dict], after: list[dict], drop: set[str] | None) -> list[dict]:
    """A longer or shorter trip keeps stops that still fit. The new length wins."""
    drop = drop or set()
    result = []
    old_by_day = {day.get("day"): day for day in before if isinstance(day, dict)}
    last = after[-1]["day"] if after else 1
    parked: list[dict] = []
    new_names_by_day: dict[Any, set[str]] = {}
    for day in after:
        if not isinstance(day, dict):
            continue
        names = {place_key(item.get("name") or "") for item in day.get("items") or []}
        new_names_by_day[day.get("day")] = names
        kept_old = []
        old = old_by_day.get(day.get("day")) or {}
        for item in old.get("items") or []:
            key = place_key(item.get("name") or "")
            if item.get("category") in drop and key not in names:
                continue
            if key and key not in names:
                kept_old.append(item)
        items = list(day.get("items") or []) + kept_old
        result.append({**day, "items": _unique(items)})
    surviving = {day.get("day") for day in result}
    for day in before:
        if day.get("day") in surviving:
            continue
        for item in day.get("items") or []:
            if item.get("category") in drop:
                continue
            parked.append(item)
    if parked and result:
        host = result[-1]
        if host.get("day") == last:
            host["items"] = _unique(list(host.get("items") or []) + parked)
    return result


def _drop_moved(merged: list[dict], before: list[dict], scoped: set[int], message: str) -> list[dict]:
    """A move is not a copy: once the place is on the destination day, it leaves the day it was on."""
    if not scoped or not re.search(r"\b(move|swap|switch|shift)\b", message or "", re.I):
        return merged
    arrived: set[str] = set()
    for day in merged:
        if day.get("day") in scoped:
            arrived |= {place_key(item.get("name") or "") for item in day.get("items") or []}
    previously_elsewhere = {
        place_key(item.get("name") or "")
        for day in before if day.get("day") not in scoped
        for item in day.get("items") or []
    }
    moving = {name for name in arrived if name and name in previously_elsewhere}
    if not moving:
        return merged
    cleaned = []
    for day in merged:
        if day.get("day") in scoped:
            cleaned.append(day)
            continue
        items = [item for item in day.get("items") or [] if place_key(item.get("name") or "") not in moving]
        cleaned.append({**day, "items": items})
    return cleaned


def _unique(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in items:
        key = place_key(item.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _prune_graph(graph: dict | None, day_plan: list[dict]) -> dict | None:
    if not graph:
        return graph
    allowed = {place_key(item.get("name") or "") for day in day_plan for item in day.get("items") or []}
    nodes = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        copy = dict(node)
        nearby = [
            place for place in copy.get("nearby_places") or []
            if isinstance(place, dict) and place_key(place.get("name") or "") in allowed
        ]
        copy["nearby_places"] = nearby
        nodes.append(copy)
    return {**graph, "nodes": nodes}
