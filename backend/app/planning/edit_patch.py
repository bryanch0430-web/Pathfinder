"""A one-place edit applied to the stored day plan, without asking a model to rewrite it."""

from __future__ import annotations

import copy
import re
from typing import Any

_SKIP = re.compile(r"\b(start over|from scratch|re-?plan|plan again)\b", re.I)
_ADD = re.compile(
    r"\b(?:add|include|put)\b\s+(?:the\s+)?(.+?)\s+"
    r"(?:to|on|onto|into|for)\s+(?:the\s+)?(?:morning|afternoon|evening\s+)?(?:of\s+)?day\s+(\d+)\b",
    re.I,
)
_REMOVE = re.compile(
    r"\b(?:remove|drop|delete)\b\s+(?:the\s+)?(.+?)\s+from\s+day\s+(\d+)\b",
    re.I,
)
_MOVE = re.compile(
    r"\bmove\b\s+(?:the\s+)?(.+?)\s+to\s+(?:the\s+)?(?:morning|afternoon|evening\s+)?(?:of\s+)?day\s+(\d+)\b",
    re.I,
)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip(" .,:;")).strip()


def _key(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (name or "").casefold())


def parse_edit(message: str, days: list[dict] | None = None) -> dict | None:
    """Return {op, name, day} for one add, remove, or move. None when the sentence is a replan."""
    text = message or ""
    if _SKIP.search(text):
        return None
    for op, pattern in (("add", _ADD), ("remove", _REMOVE), ("move", _MOVE)):
        match = pattern.search(text)
        if not match:
            continue
        name = _clean(match.group(1))
        day = int(match.group(2))
        if len(name) < 2 or day < 1:
            return None
        if days is not None and day > len(days) and op != "add":
            return None
        return {"op": op, "name": name, "day": day}
    return None


def apply_edit(days: list[dict], edit: dict) -> tuple[list[dict], list[str]]:
    """Copy the plan and change one stop. Other days stay the same objects' contents."""
    updated = copy.deepcopy(days)
    target = next((day for day in updated if day.get("day") == edit["day"]), None)
    if target is None:
        return days, []
    name = edit["name"]
    key = _key(name)
    if edit["op"] == "add":
        items = target.setdefault("items", [])
        if any(_key(item.get("name") or "") == key for item in items):
            return updated, [f"{name} is already on day {edit['day']}."]
        items.append({
            "name": name, "category": "sight", "time_of_day": None, "option": False, "duration_hours": 1.5,
        })
        return updated, [f"Added {name} to day {edit['day']}. The other days are unchanged."]
    if edit["op"] == "remove":
        before = len(target.get("items") or [])
        target["items"] = [item for item in target.get("items") or [] if _key(item.get("name") or "") != key]
        if len(target["items"]) == before:
            return days, []
        return updated, [f"Removed {name} from day {edit['day']}. The other days are unchanged."]
    found = None
    for day in updated:
        kept = []
        for item in day.get("items") or []:
            if _key(item.get("name") or "") == key and found is None:
                found = item
                continue
            kept.append(item)
        day["items"] = kept
    if found is None:
        return days, []
    found["time_of_day"] = None
    target.setdefault("items", []).append(found)
    return updated, [f"Moved {name} to day {edit['day']}. The other stops stayed where they were."]


def graph_from_days(days: list[dict], *, origin: str | None, destination: str) -> dict:
    """A map the studio can draw from the stored days, without another model call."""
    nodes: list[dict[str, Any]] = []
    if origin:
        nodes.append({"id": "origin", "name": origin, "kind": "origin", "day_start": None, "day_end": None, "nearby_places": []})
    edges = []
    previous = "origin" if origin else None
    for day in days:
        for index, item in enumerate(day.get("items") or []):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            node_id = f"d{day.get('day')}-{index}"
            nodes.append({
                "id": node_id, "name": name, "kind": "stop",
                "day_start": day.get("day"), "day_end": day.get("day"), "nearby_places": [],
            })
            if previous:
                edges.append({"source": previous, "target": node_id})
            previous = node_id
    if not any(node.get("kind") == "stop" for node in nodes):
        nodes.append({"id": "destination", "name": destination, "kind": "stop", "day_start": 1, "day_end": 1, "nearby_places": []})
    return {"nodes": nodes, "edges": edges}
