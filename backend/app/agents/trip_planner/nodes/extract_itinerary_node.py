"""Turn the generated itinerary into a small, evidence-backed route graph."""

import json
import logging
import re
import unicodedata

from app.agents.trip_planner.state import TripPlanningState
from app.services.llm.service import llm_service

logger = logging.getLogger(__name__)

EXTRACTION_INSTRUCTION = """Extract the ordered travel stops from the itinerary.
Return JSON only: {"stops":[{"name":"...","day_start":1,"day_end":2,
"nearby_places":[{"name":"...","category":"attraction"}]}],
"route_order":["exact origin name","exact stop name","exact stop name","exact origin name"],
"legs":[{"from":"...","to":"...","mode":null,"duration":null,"cost":null,"distance":null}],
"days":[{"day":1,"base":"overnight city","items":[{"name":"...","category":"sight","time_of_day":"morning","start":"09:00","end":"11:00"}]}]}.
Top-level stops are only the day-by-day base cities and explicitly visited
transfer hubs, in journey order. Put beaches, attractions, neighborhoods,
restaurants, optional alternatives, and day trips in nearby_places instead.
Nearby places must be attractions, neighborhoods, or restaurants explicitly
named in the itinerary for that stop; never guess proximity or add optional detours.
Do not include the departure origin as a stop. Include return legs to the origin
and revisits to earlier stops when the text describes them. Use null for facts absent from
the text. Never invent distances, times, prices, transport modes, or places.
Route order may repeat an existing stop; include only moves the draft actually makes.
"days" lists every trip day once, in order: its overnight base and the places the itinerary
plans that day. category is one of sight, food, nature, experience, shopping, nightlife,
transit, stay, other; time_of_day is morning, afternoon, evening, or null if the text doesn't say.
start and end are 24-hour HH:MM planning slots copied from the itinerary, or null when it gives none.
Keep each day's items in visit order. The overnight stay is an item with category stay.
When a day offers alternatives ("Option A: Nikko, Option B: Kamakura"), list each one with "option": true.
"""

DAY_CATEGORIES = {"sight", "food", "nature", "experience", "shopping", "nightlife", "transit", "stay", "other"}
TIMES_OF_DAY = {"morning", "afternoon", "evening"}
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def clock_text(value: object) -> str | None:
    """A planning slot as HH:MM, or None when the text did not give one."""
    match = _CLOCK.match(str(value or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _slot_for(clock: str | None) -> str | None:
    if not clock:
        return None
    hour = int(clock[:2])
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def normalize_day_plan(raw: object, duration_days: int, graph: dict | None, destination: str) -> list[dict]:
    """Exactly one entry per trip day. Days the model skipped fall back to the graph stop covering them."""
    by_day: dict[int, dict] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        day = entry.get("day")
        if type(day) is not int or not 1 <= day <= duration_days or day in by_day:
            continue
        items = []
        for item in (entry.get("items") or [])[:12] if isinstance(entry.get("items"), list) else []:
            name = " ".join(str(item.get("name") or "").split())[:120] if isinstance(item, dict) else ""
            if not name or any(existing["name"].casefold() == name.casefold() for existing in items):
                continue
            category = str(item.get("category") or "other").lower()
            start = clock_text(item.get("start"))
            end = clock_text(item.get("end"))
            if not start or (end and end <= start):
                end = None
            time = str(item.get("time_of_day") or "").lower()
            if time not in TIMES_OF_DAY:
                time = _slot_for(start)
            items.append({"name": name, "category": category if category in DAY_CATEGORIES else "other",
                          "time_of_day": time, "start": start, "end": end, "option": item.get("option") is True})
        base = " ".join(str(entry.get("base") or "").split())[:120]
        by_day[day] = {"day": day, "base": base, "items": items}
    stops = [node for node in (graph or {}).get("nodes", []) if node.get("kind") == "stop"]

    def stop_for(day: int) -> str:
        return next((node["name"] for node in stops if type(node.get("day_start")) is int
                     and node["day_start"] <= day <= (node.get("day_end") or node["day_start"])), destination)

    return [{**by_day.get(day, {"day": day, "base": "", "items": []}),
             "base": by_day.get(day, {}).get("base") or stop_for(day)} for day in range(1, duration_days + 1)]


def place_key(name: str) -> str:
    """One key for one place however the model typed it: "Kiyomizu‑dera" (U+2011) is "Kiyomizu-dera",
    "Café" is "Cafe"."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).casefold()
    # "Kinkaku-ji (Golden Pavilion)" is "Kinkaku-ji"; "Gion district" is "Gion".
    plain = re.sub(r"\(.*?\)|\b(district|area|neighbou?rhood)\s*$", "", plain.strip())
    return re.sub(r"[^0-9a-z]+", "", plain)


def day_plan_changes(before: list[dict], after: list[dict], limit: int = 12) -> list[dict]:
    """What a revision changed in the day plan the studio shows: the receipt under the reply."""
    def days_of(plan: list[dict]) -> dict[str, tuple[str, list[int]]]:
        found: dict[str, tuple[str, list[int]]] = {}
        for day in plan:
            for item in day.get("items") or []:
                name = found.setdefault(place_key(item["name"]), (item["name"], []))
                name[1].append(day["day"])
        return found

    old, new = days_of(before), days_of(after)
    moved, added, removed = [], [], []
    for key, (name, days) in new.items():
        was = old.get(key, (name, []))[1]
        if len(was) == len(days) == 1 and was != days:
            moved.append(f"Moved {name} from day {was[0]} to day {days[0]}")
        else:
            added += [f"Added {name} to day {day}" for day in days if day not in was]
    for key, (name, days) in old.items():
        now = new.get(key, (name, []))[1]
        if not (len(days) == len(now) == 1):
            removed += [f"Removed {name} from day {day}" for day in days if day not in now]
    bases = [f"Day {b['day']} now based in {b['base']} (was {a['base']})" for a, b in zip(before, after)
             if a["day"] == b["day"] and a.get("base") and b.get("base") and a["base"].casefold() != b["base"].casefold()]
    lines = moved + added + removed + bases
    extra = [f"…and {len(lines) - limit} more changes"] if len(lines) > limit else []
    return [{"status": "done", "text": line} for line in lines[:limit] + extra]


def _evidence_line(markdown: str, name: str) -> str | None:
    for line in markdown.splitlines():
        if name.casefold() in line.casefold():
            return re.sub(r"^[\s#>*|\-]+|[\s|]+$", "", line).strip()[:220]
    return None


def _stop_evidence_line(markdown: str, name: str) -> str | None:
    for line in markdown.splitlines():
        if name.casefold() in line.casefold() and re.search(r"\bDays?\s*\d+|^\s*\|\s*\d+\s*\|", line, re.I):
            return re.sub(r"^[\s#>*|\-]+|[\s|]+$", "", line).strip()[:220]
    return _evidence_line(markdown, name)


def _nearby_places(markdown: str, stop: str, raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    places = []
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 120 or name.casefold() == stop.casefold():
            continue
        line = next((line for line in markdown.splitlines()
                     if name.casefold() in line.casefold() and stop.casefold() in line.casefold()), None)
        if not line or any(place["name"].casefold() == name.casefold() for place in places):
            continue
        category = str(item.get("category") or "other").lower()
        if category not in {"attraction", "restaurant", "neighborhood", "other"}:
            category = "other"
        places.append({"name": name, "category": category,
                       "evidence": re.sub(r"^[\s#>*|\-]+|[\s|]+$", "", line).strip()[:220]})
        if len(places) == 8:
            break
    return places


def _route_base_lines(markdown: str) -> list[str]:
    """Read the base/move column and day headings, excluding highlight columns."""
    lines = []
    for line in markdown.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and re.match(r"(?:Day\s*)?\d+\b", cells[0].strip("* "), re.I):
            lines.append(cells[1])
        elif re.match(r"^\s*#{1,4}\s*Days?\s*\d+", line, re.I):
            lines.append(line)
        elif ("→" in line or "->" in line or "✈" in line) and "|" not in line:
            lines.append(line)
    return lines


def _day_rows(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines()
            if re.match(r"^\s*\|\s*\**Day\s*\d+\b", line, re.I)]


def _day_row_details(markdown: str, stop: str, max_days: int) -> tuple[int | None, int | None, list[dict]]:
    """Recover visit days and named highlights when model extraction is unavailable."""
    alias = re.sub(r"\s+Airport$", "", stop.split(",", 1)[0], flags=re.I)
    days = []
    nearby = []
    for row in _day_rows(markdown):
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) < 3 or not re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", row, re.I):
            continue
        day_match = re.search(r"\bDay\s*(\d+)", cells[0], re.I)
        day = _day(int(day_match.group(1)), max_days) if day_match else None
        if day is not None:
            days.append(day)
        if not re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", cells[1], re.I):
            continue
        for match in re.finditer(r"\*\*([^*]{2,80})\*\*", "|".join(cells[2:])):
            name = match.group(1).strip(" .,:;–—")
            if (not name or name.casefold() == stop.casefold() or match.group(1).strip().endswith(":")
                    or not name[0].isupper() or re.search(r"\d", name)
                    or name.casefold() in {"morning", "afternoon", "evening", "lunch", "early", "tip"}
                    or any(place["name"].casefold() == name.casefold() for place in nearby)):
                continue
            category = "restaurant" if re.search(r"restaurant|cafe|café|eatery", name, re.I) else "attraction"
            nearby.append({"name": name, "category": category,
                           "evidence": re.sub(r"^[\s#>*|\-]+|[\s|]+$", "", row).strip()[:220]})
            if len(nearby) >= 8:
                break
    return (min(days) if days else None, max(days) if days else None, nearby)


def _day(value: object, max_days: int) -> int | None:
    return value if type(value) is int and 1 <= value <= max_days else None


def build_itinerary_graph(
    markdown: str,
    origin: str | None,
    destination: str,
    duration_days: int,
    candidates: list[dict] | None = None,
    requested_places: list[str] | None = None,
    extracted: dict | None = None,
) -> dict:
    """Accept only stops supported by the displayed text; fall back to known candidates."""
    raw_stops = extracted.get("stops", []) if isinstance(extracted, dict) else []
    if not isinstance(raw_stops, list):
        raw_stops = []
    base_lines = _route_base_lines(markdown)
    names: list[tuple[str, int | None, int | None, list[dict]]] = []
    for item in raw_stops[:24]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 120 or not _evidence_line(markdown, name):
            continue
        if base_lines and not any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", line, re.I) for line in base_lines):
            continue
        if name.casefold() not in {row[0].casefold() for row in names}:
            start = _day(item.get("day_start"), duration_days)
            end = _day(item.get("day_end"), duration_days)
            names.append((name, start, end if start is None or end is None or end >= start else start,
                          _nearby_places(markdown, name, item.get("nearby_places"))))

    if not names:
        known = [str(item.get("name") or "").strip() for item in (candidates or []) if isinstance(item, dict)]
        known += requested_places or []
        found = []
        for name in dict.fromkeys(known):
            if base_lines and not any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", line, re.I) for line in base_lines):
                continue
            line = _evidence_line(markdown, name) if name else None
            if line:
                position = markdown.casefold().find(name.casefold())
                day_match = re.search(r"\bDays?\s*(\d+)(?:\s*[-–—]\s*(\d+))?", line, re.I)
                start = _day(int(day_match.group(1)), duration_days) if day_match else None
                end = _day(int(day_match.group(2)), duration_days) if day_match and day_match.group(2) else start
                found.append((position, name, start, end))
        names = [(name, start, end, []) for _, name, start, end in sorted(found)[:24]]
    if not names:
        names = [(destination, None, None, [])]

    # Explicitly named transfer hubs can be missed by the model when they occur
    # inside a highlights cell rather than the base/move cell.
    for row in _day_rows(markdown):
        for match in re.finditer(r"\b[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,2}\s+(?:Airport|Station)\b", row):
            hub = match.group().strip()
            if any(hub.casefold() == name.casefold() or hub.casefold().removesuffix(" airport") == name.casefold()
                   for name, *_ in names):
                continue
            day_match = re.search(r"\bDay\s*(\d+)", row, re.I)
            day = _day(int(day_match.group(1)), duration_days) if day_match else None
            names.append((hub, day, day, []))

    nodes = []
    if origin:
        nodes.append({"id": "origin", "name": origin, "kind": "origin", "day_start": None, "day_end": None,
                      "evidence": "Departure point supplied by the traveler", "nearby_places": []})
    for index, (name, start, end, nearby) in enumerate(names, 1):
        inferred_start, inferred_end, inferred_nearby = _day_row_details(markdown, name, duration_days)
        nodes.append({"id": f"stop-{index}", "name": name, "kind": "stop",
                      "day_start": min(day for day in (start, inferred_start) if day is not None)
                      if start is not None or inferred_start is not None else None,
                      "day_end": max(day for day in (end, inferred_end) if day is not None)
                      if end is not None or inferred_end is not None else None,
                      "evidence": _stop_evidence_line(markdown, name),
                      "nearby_places": nearby or inferred_nearby})
    stop_names = {node["name"].casefold() for node in nodes}
    for node in nodes:
        node["nearby_places"] = [place for place in node["nearby_places"]
                                 if place["name"].casefold() not in stop_names]

    raw_legs = extracted.get("legs", []) if isinstance(extracted, dict) else []
    if not isinstance(raw_legs, list):
        raw_legs = []
    edges = []
    def comparable(value: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())

    def table_leg_fact(source: dict, target: dict, field: str) -> str | None:
        source_name = source["name"].split(",", 1)[0]
        target_name = re.sub(r"\s+Airport$", "", target["name"].split(",", 1)[0], flags=re.I)
        for line in markdown.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 3 or "→" not in cells[0]:
                continue
            route = cells[0].casefold()
            if (source_name.casefold() not in route or target_name.casefold() not in route
                    or route.index(source_name.casefold()) >= route.index(target_name.casefold())):
                continue
            value = cells[1] if field == "mode" else cells[2] if field == "duration" else ""
            if value and not value.startswith("---") and (field != "duration" or re.search(r"\d", value)):
                return value[:100]
        return None

    def append_edge(source: dict, target: dict) -> None:
        match = next((leg for leg in raw_legs if isinstance(leg, dict)
                      and str(leg.get("from", "")).casefold() == source["name"].casefold()
                      and str(leg.get("to", "")).casefold() == target["name"].casefold()), {})
        def supported(field: str) -> str | None:
            value = match.get(field)
            return value.strip() if isinstance(value, str) and value.strip() and comparable(value.strip()) in comparable(markdown) else None
        edges.append({"id": f"leg-{len(edges) + 1}", "source": source["id"], "target": target["id"],
                      "mode": supported("mode") or table_leg_fact(source, target, "mode"),
                      "duration": supported("duration") or table_leg_fact(source, target, "duration"),
                      "cost": supported("cost"), "distance": supported("distance")})

    raw_order = extracted.get("route_order", []) if isinstance(extracted, dict) else []
    path = []
    # Day table rows are the displayed journey. Reading named stops in their
    # written order preserves transfer hubs and return visits without inventing
    # a connection between two non-adjacent places.
    def node_alias(node: dict) -> str:
        return re.sub(r"\s+Airport$", "", node["name"].split(",", 1)[0], flags=re.I)

    for row in _day_rows(markdown):
        mentions = sorted(((match.start(), node) for node in nodes
                           for match in re.finditer(
                               rf"(?<!\w){re.escape(node_alias(node))}(?!\w)",
                               row, re.I)),
                          key=lambda item: item[0])
        seen_in_day: set[str] = set()
        for _, node in mentions:
            if node["id"] in seen_in_day:
                continue
            seen_in_day.add(node["id"])
            if not path or path[-1]["id"] != node["id"]:
                path.append(node)
    if {node["id"] for node in path} != {node["id"] for node in nodes}:
        path = []
    model_path = []
    if isinstance(raw_order, list):
        for value in raw_order[:32]:
            name = str(value).strip().casefold()
            node = next((node for node in nodes if name in {
                node["name"].casefold(), node["name"].split(",", 1)[0].casefold(),
                re.sub(r"\s+Airport$", "", node["name"], flags=re.I).casefold(),
            }), None)
            if node and (not model_path or model_path[-1]["id"] != node["id"]):
                model_path.append(node)
            elif not node:
                model_path = []
                break
    if {node["id"] for node in model_path} == {node["id"] for node in nodes} and len(model_path) > len(path):
        path = model_path
    if not path or {node["id"] for node in path} != {node["id"] for node in nodes}:
        path = nodes
    else:
        nodes = list({node["id"]: node for node in path}.values())
    for source, target in zip(path, path[1:]):
        append_edge(source, target)

    # A compact route line or final-day table row can explicitly return through
    # an earlier stop and then the origin. Preserve that loop without duplicating nodes.
    if path is nodes and origin and len(nodes) > 2:
        best_return: list[dict] = []
        route_text = markdown.split("Optional Extras", 1)[0]
        for line in route_text.splitlines():
            if "→" not in line and "->" not in line:
                continue
            parts = re.split(r"\s*(?:→|->|✈)\s*", line)
            mapped = []
            for part in parts:
                found = next((node for node in nodes if
                              re.search(rf"(?<!\w){re.escape(node['name'].split(',', 1)[0])}(?!\w)", part, re.I)), None)
                mapped.append(found)
            last_index = next((i for i, node in enumerate(mapped) if node and node["id"] == nodes[-1]["id"]), -1)
            if last_index < 0:
                continue
            return_path = mapped[last_index:]
            if len(return_path) > len(best_return) and all(return_path) and return_path[-1]["id"] == "origin":
                best_return = return_path
        for source, target in zip(best_return, best_return[1:]):
            append_edge(source, target)
    return {"version": 8, "nodes": nodes, "edges": edges}


async def extract_itinerary(state: TripPlanningState) -> dict:
    markdown = state.get("assistant_response") or ""
    extracted = None
    # A revision passes the previous plan's names, so a place still in the text keeps its name and isn't
    # split or merged differently (which would show up as a change that never happened).
    known = state.get("known_places") or []
    reuse = (f"KNOWN PLACES (use these exact names for places the itinerary still mentions; don't merge or split them): "
             f"{json.dumps(known, ensure_ascii=False)}\n\n") if known else ""
    # Two tries: an occasional empty or chatty reply would otherwise leave the studio on the old days.
    for attempt in range(2 if markdown else 0):
        try:
            raw = await llm_service.generate(
                prompt=f"Destination: {state.get('destination')}\nTrip days: {state.get('duration_days')}\n\n"
                       f"{reuse}ITINERARY:\n{markdown[:18000]}",
                system_instruction=EXTRACTION_INSTRUCTION, temperature=0.0,
            )
            extracted = json.loads(re.search(r"\{[\s\S]*\}", raw or "").group(0))
            break
        except Exception as exc:
            logger.info("Itinerary extraction attempt %d failed: %s", attempt + 1, exc)
    if extracted is None and markdown:
        logger.info("Using deterministic itinerary graph fallback")
    graph = build_itinerary_graph(
        markdown=markdown, origin=state.get("origin"), destination=state.get("destination") or "Trip",
        duration_days=state.get("duration_days") or 1, candidates=state.get("candidates"),
        requested_places=state.get("places_to_visit"), extracted=extracted,
    )
    days = extracted.get("days") if isinstance(extracted, dict) else None
    # No day plan when the model gave none (e.g. both LLM providers were busy): a list of empty
    # days would be saved over a good plan. Callers keep the previous one or fall back to the graph.
    day_plan = normalize_day_plan(days, state.get("duration_days") or 1, graph, state.get("destination") or "Trip") \
        if isinstance(days, list) and days else None
    return {"itinerary_graph": graph, "day_plan": day_plan}
