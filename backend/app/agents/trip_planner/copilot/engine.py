"""Deterministic core of build-with-agent mode.

Budget math, routing cost, day capacity, preference filtering, ranking and
money-saving moves all live here. LLMs only propose estimates and words; every
number the traveler sees is computed by these functions.

Copilot state (persisted in the assistant message payload under "copilot"):
    status: "building" | "complete"
    current_day: int
    destination, currency: str
    budget: float | None                      # total trip budget in `currency`
    profile: {pace, travel_mode, vibe[], hard{must[], avoid[]}, soft{likes[], dislikes[]}, rejected[]}
    rates: {transfer{walk, transit, taxi, drive}, stay_per_night, food_per_day}
    days: [{day, base, items[item]}]
    pool: [item]                              # researched candidates, community-first
    researched: [query]                       # queries already run, prevents loops
    last_suggestions: [name]                  # lets "the second one" resolve to a place

item: {name, base, area, category, tags[], est_cost, duration_hours, why, source, basis}
    basis: "community" (traveler posts) | "model_estimate" | "traveler" (user-set)
"""

import math
import re
from typing import Any
from app.services import route_reality

PACE_HOURS = {"relaxed": 6.0, "balanced": 8.0, "packed": 11.0}
MODES = ("walk", "transit", "taxi", "drive")
# Used only when a place has no verified coordinates; shown as provisional.
HOP_MINUTES = {"walk": 30, "transit": 35, "taxi": 25, "drive": 30}
CURRENCIES = {"INR", "JPY", "USD", "EUR", "GBP", "AUD", "CAD"}
COMFORT = ("budget", "mid_range", "comfortable")
PREF_LISTS = {"must": ("hard", "must"), "avoid": ("hard", "avoid"),
              "like": ("soft", "likes"), "dislike": ("soft", "dislikes")}
OPPOSITE = {"must": "avoid", "avoid": "must", "like": "dislike", "dislike": "like"}
# How a saved preference reads in the receipt under the reply.
PREF_SAVED = {"must": "Rule saved: {} is a must", "avoid": "Rule saved: no {}",
              "like": "Noted: you like {}", "dislike": "Noted: you'd rather skip {}"}
POOL_LIMIT = 60
_STOP = {"with", "and", "the", "long", "late", "early", "very", "lots", "much", "many", "heavy",
         "places", "place", "things", "stuff", "spots", "areas", "area", "too", "any", "more"}


def key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def clean(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def new_copilot(*, destination: str, duration_days: int, currency: str | None,
                preferences: dict | None, places: list[str] | None,
                budget_target: float | None, overrides: dict | None) -> dict:
    prefs, overrides = preferences or {}, overrides or {}
    currency = overrides.get("currency") or currency
    mode = overrides.get("travel_mode") or prefs.get("travel_mode")
    travelers = max(1, int(prefs.get("adults") or 1) + int(prefs.get("children") or 0))
    return {
        "status": "building",
        "current_day": 1,
        "destination": destination,
        "currency": currency if currency in CURRENCIES else "INR",
        "budget": number(overrides.get("budget_amount")) or number(budget_target),
        "profile": {
            "pace": prefs.get("pace") if prefs.get("pace") in PACE_HOURS else "balanced",
            "travel_mode": mode if mode in MODES else "transit",
            "travelers": travelers,
            "comfort": prefs.get("comfort") if prefs.get("comfort") in COMFORT else "mid_range",
            "vibe": [clean(v, 40) for v in overrides.get("vibe") or [] if clean(v, 40)][:5],
            "hard": {"must": list(places or []), "avoid": list(prefs.get("avoid") or [])},
            "soft": {"likes": list(prefs.get("interests") or []), "dislikes": []},
            "rejected": [],
        },
        "rates": {"transfer": {"walk": 0.0, "transit": None, "taxi": None, "drive": None},
                  "stay_per_night": None, "food_per_day": None},
        "days": [{"day": day, "base": destination, "items": []} for day in range(1, duration_days + 1)],
        "pool": [],
        "researched": [],
        "last_suggestions": [],
        "route_points": {}, "route_queries": [], "route_times": {}, "route_requests": [],
    }


def make_item(raw: dict, basis: str = "model_estimate") -> dict | None:
    name = clean(raw.get("name"))
    if not name:
        return None
    hours = number(raw.get("duration_hours"))
    coordinates = route_reality.point(raw)
    return {
        "name": name,
        "base": clean(raw.get("base")) or None,
        "area": clean(raw.get("area")) or None,
        "category": clean(raw.get("category"), 40).lower() or "attraction",
        "tags": [clean(tag, 30).lower() for tag in (raw.get("tags") or [])[:8] if clean(tag, 30)],
        "est_cost": number(raw.get("est_cost")),
        "duration_hours": min(hours, 12.0) if hours else 2.0,
        "time_of_day": raw.get("time_of_day") if raw.get("time_of_day") in ("morning", "afternoon", "evening") else None,
        **({"lat": coordinates[0], "lon": coordinates[1]} if coordinates else {}),
        "why": clean(raw.get("why"), 300),
        "source": clean(raw.get("source"), 300) or None,
        "basis": basis,
    }


def merge_pool(copilot: dict, items: list[dict]) -> int:
    known = {key(item["name"]) for item in copilot["pool"]}
    added = 0
    for item in items:
        if item and key(item["name"]) not in known:
            copilot["pool"].append(item)
            known.add(key(item["name"]))
            added += 1
    del copilot["pool"][POOL_LIMIT:]
    return added


def day_of(copilot: dict, day: int) -> dict:
    return copilot["days"][max(1, min(day, len(copilot["days"]))) - 1]


def find(items: list[dict], name: str) -> dict | None:
    wanted = key(name)
    if not wanted:
        return None
    return (next((item for item in items if key(item["name"]) == wanted), None)
            or next((item for item in items if wanted in key(item["name"]) or key(item["name"]) in wanted), None))


def scheduled(copilot: dict) -> list[tuple[dict, dict]]:
    return [(day, item) for day in copilot["days"] for item in day["items"]]


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------

def _stems(term: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", key(term)) if len(w) >= 3 and w not in _STOP]
    return [w[:max(4, len(w) - 2)] for w in words]


def matches(term: str, item: dict) -> bool:
    """ponytail: keyword stems ("hikes" ~ "hiking"); swap for embeddings if misses show up."""
    text = key(" ".join([item["name"], item.get("category") or "", item.get("area") or "",
                         " ".join(item.get("tags") or []), item.get("why") or ""]))
    return any(stem in text for stem in _stems(term))


def hard_conflicts(profile: dict, item: dict) -> list[str]:
    if any(key(item["name"]) == key(must) for must in profile["hard"]["must"]):
        return []
    return [term for term in profile["hard"]["avoid"] if matches(term, item)]


def score(copilot: dict, item: dict, day: dict) -> float:
    profile = copilot["profile"]
    value = 3.0 * sum(matches(t, item) for t in profile["soft"]["likes"])
    value -= 3.0 * sum(matches(t, item) for t in profile["soft"]["dislikes"])
    value += 2.0 * sum(matches(t, item) for t in profile["vibe"])
    value += 6.0 * any(key(item["name"]) == key(m) for m in profile["hard"]["must"])
    value += 1.0 * (item["basis"] == "community")
    value += 1.0 * any(key(i.get("area")) == key(item.get("area")) for i in day["items"] if item.get("area"))
    return value


# --------------------------------------------------------------------------
# Money and time
# --------------------------------------------------------------------------

def hop_cost(copilot: dict) -> float | None:
    return copilot["rates"]["transfer"].get(copilot["profile"]["travel_mode"])


def _hops(items: list[dict]) -> int:
    areas = [key(item.get("area")) for item in items if item.get("area")]
    return sum(1 for a, b in zip(areas, areas[1:]) if a != b)


def _party(copilot: dict) -> tuple[int, int]:
    """(people, hotel rooms). Estimates are per person; rooms sleep two.
    ponytail: flat 2-per-room; ask for room count if families complain."""
    people = copilot["profile"].get("travelers") or 1
    return people, -(-people // 2)


def day_cost(copilot: dict, items: list[dict]) -> float:
    people, _ = _party(copilot)
    return (sum(item["est_cost"] or 0 for item in items) + _hops(items) * (hop_cost(copilot) or 0)) * people


def day_hours(copilot: dict, items: list[dict], base: str | None = None) -> float:
    mode = copilot["profile"]["travel_mode"]
    if base:
        travel = route_reality.day_travel(copilot, base, items, mode, HOP_MINUTES[mode])
        return sum(item["duration_hours"] for item in items) + travel["hours"]
    return sum(item["duration_hours"] for item in items) + _hops(items) * HOP_MINUTES[mode] / 60


def baseline(copilot: dict) -> float:
    days = len(copilot["days"])
    rates = copilot["rates"]
    people, rooms = _party(copilot)
    return (rates["stay_per_night"] or 0) * max(days - 1, 0) * rooms + (rates["food_per_day"] or 0) * days * people


def budget_status(copilot: dict) -> dict:
    by_day = []
    for day in copilot["days"]:
        travel = route_reality.day_travel(copilot, day["base"], day["items"], copilot["profile"]["travel_mode"],
                                          HOP_MINUTES[copilot["profile"]["travel_mode"]])
        by_day.append({"day": day["day"], "base": day["base"],
                       "cost": round(day_cost(copilot, day["items"]), 2),
                       "hours": round(day_hours(copilot, day["items"], day["base"]), 1),
                       "travel_hours": travel["hours"], "distance_km": travel["distance_km"],
                       "unverified_legs": travel["unverified_legs"], "travel_basis": travel["basis"],
                       "capacity_hours": PACE_HOURS[copilot["profile"]["pace"]]})
    committed = round(baseline(copilot) + sum(day["cost"] for day in by_day)
                      + (copilot.get("ledger_adjustment") or 0), 2)
    budget = copilot["budget"]
    missing = [name for name, value in (("stay_per_night", copilot["rates"]["stay_per_night"]),
                                        ("food_per_day", copilot["rates"]["food_per_day"]),
                                        ("transfer", hop_cost(copilot))) if value is None]
    return {
        "currency": copilot["currency"],
        "budget": budget,
        "committed": committed,
        "baseline": round(baseline(copilot), 2),
        "remaining": round(budget - committed, 2) if budget is not None else None,
        "over": budget is not None and committed > budget,
        "by_day": by_day,
        "unpriced_items": [item["name"] for _, item in scheduled(copilot) if item["est_cost"] is None],
        "unknown_rates": missing,
    }


def check_add(copilot: dict, day_no: int, item: dict) -> dict:
    """Would adding `item` to `day_no` break a hard preference, the day's hours, or the budget?"""
    day = day_of(copilot, day_no)
    after = [*day["items"], item]
    delta = round(day_cost(copilot, after) - day_cost(copilot, day["items"]), 2)
    hours = day_hours(copilot, after, day["base"])
    reasons = []
    conflicts = hard_conflicts(copilot["profile"], item)
    if conflicts:
        reasons.append({"code": "hard_avoid", "detail": conflicts})
    if any(key(item["name"]) == key(name) for name in copilot["profile"]["rejected"]):
        reasons.append({"code": "rejected_before"})
    capacity = PACE_HOURS[copilot["profile"]["pace"]]
    travel = route_reality.day_travel(copilot, day["base"], after, copilot["profile"]["travel_mode"],
                                      HOP_MINUTES[copilot["profile"]["travel_mode"]])
    direct = route_reality.leg(copilot, route_reality.base_point(copilot, day["base"]),
                               route_reality.item_point(copilot, item), copilot["profile"]["travel_mode"])
    if travel["known_hours"] >= capacity or (item.get("time_of_day") in ("afternoon", "evening")
                                     and direct and direct["hours"] > 3.5):
        reasons.append({"code": "unreachable", "from": day["base"],
                        "one_way_hours": direct["hours"] if direct else None,
                        "travel_hours": travel["hours"], "distance_km": travel["distance_km"]})
    if hours > capacity:
        reasons.append({"code": "day_full", "hours_after": round(hours, 1), "capacity_hours": capacity})
    status = budget_status(copilot)
    # Free additions always fit, even when stay and food alone already exceed the budget.
    if status["budget"] is not None and delta > 0 and status["committed"] + delta > status["budget"]:
        reasons.append({"code": "over_budget",
                        "overshoot": round(status["committed"] + delta - status["budget"], 2)})
    return {"ok": not reasons, "reasons": reasons, "cost_delta": delta, "hours_after": round(hours, 1)}


# --------------------------------------------------------------------------
# Recommendation engine
# --------------------------------------------------------------------------

def _same_base(copilot: dict, item: dict, day: dict) -> bool:
    return (not item.get("base") or key(day["base"]) == key(copilot["destination"])
            or key(item["base"]) == key(day["base"]))


def rank(copilot: dict, day_no: int, limit: int = 3, *, cheaper_than: float | None = None,
         category: str | None = None) -> list[dict]:
    """Best pool items for a day that respect hard prefs, day hours and the remaining budget."""
    day = day_of(copilot, day_no)
    taken = {key(item["name"]) for _, item in scheduled(copilot)}
    options = []
    for item in copilot["pool"]:
        if key(item["name"]) in taken or not _same_base(copilot, item, day):
            continue
        if cheaper_than is not None and (item["est_cost"] or 0) >= cheaper_than:
            continue
        fit = check_add(copilot, day_no, item)
        if not fit["ok"]:
            continue
        options.append((category is not None and item["category"] == category,
                        score(copilot, item, day), -(item["est_cost"] or 0), item, fit))
    options.sort(key=lambda option: option[:3], reverse=True)
    return [{**item, "cost_delta": fit["cost_delta"], "match_score": round(value, 1)}
            for _, value, _, item, fit in options[:limit]]


def other_days_that_fit(copilot: dict, item: dict, exclude: int) -> list[int]:
    return [day["day"] for day in copilot["days"]
            if day["day"] != exclude and _same_base(copilot, item, day)
            and check_add(copilot, day["day"], item)["ok"]][:3]


def move_suggestions(copilot: dict, limit: int = 2) -> list[dict]:
    """Moves that remove a cross-area hop: same plan, cheaper and shorter days."""
    minutes = HOP_MINUTES[copilot["profile"]["travel_mode"]]
    found = []
    for day, item in scheduled(copilot):
        without = [i for i in day["items"] if i is not item]
        freed = day_cost(copilot, day["items"]) - day_cost(copilot, without)
        freed_hops = _hops(day["items"]) - _hops(without)
        if freed_hops <= 0:
            continue
        for target in copilot["days"]:
            if target is day or not _same_base(copilot, item, target):
                continue
            if not any(key(i.get("area")) == key(item.get("area")) for i in target["items"]):
                continue
            after = [*target["items"], item]
            added = day_cost(copilot, after) - day_cost(copilot, target["items"])
            added_hops = _hops(after) - _hops(target["items"])
            if day_hours(copilot, after, target["base"]) > PACE_HOURS[copilot["profile"]["pace"]] or added_hops >= freed_hops:
                continue
            found.append({"name": item["name"], "from_day": day["day"], "to_day": target["day"],
                          "saves": round(freed - added, 2),
                          "saves_minutes": (freed_hops - added_hops) * minutes})
    found.sort(key=lambda move: (move["saves"], move["saves_minutes"]), reverse=True)
    return found[:limit]


def alternatives_for(copilot: dict, blocked: dict) -> dict:
    item, day_no = blocked["item"], blocked["day"]
    codes = {reason["code"] for reason in blocked["reasons"]}
    return {
        **blocked,
        "alternatives": rank(copilot, day_no, 3, category=item["category"],
                             cheaper_than=item["est_cost"] if "over_budget" in codes and item["est_cost"] else None),
        "fits_on_days": other_days_that_fit(copilot, item, day_no) if codes <= {"day_full", "over_budget", "unreachable"} else [],
    }


# --------------------------------------------------------------------------
# Applying traveler decisions
# --------------------------------------------------------------------------

def _day_arg(copilot: dict, value: Any, default: int) -> int:
    return value if type(value) is int and 1 <= value <= len(copilot["days"]) else default


# The traveler's own rules still ask before an add; day hours and the budget only warn.
ASK_FIRST = {"hard_avoid", "rejected_before"}


def _heads_up(copilot: dict, day_no: int) -> str:
    """What an edit the traveler asked for strained, said in the event itself."""
    status, day = budget_status(copilot), day_of(copilot, day_no)
    notes = []
    hours, capacity = round(day_hours(copilot, day["items"], day["base"]), 1), PACE_HOURS[copilot["profile"]["pace"]]
    if hours > capacity:
        notes.append(f"day {day_no} is now {hours:g} h, over the {capacity:g} h a {copilot['profile']['pace']} day holds")
    if status["over"]:
        notes.append(f"the trip is ~{status['committed'] - status['budget']:g} {copilot['currency']} over budget")
    return f" (heads-up: {'; '.join(notes)})" if notes else ""


def apply_ops(copilot: dict, ops: list[Any]) -> tuple[list[str], list[dict], list[str]]:
    """Apply structured traveler decisions. Returns (events, blocked adds, research queries).

    Ops come from the LLM or the client, so every field is validated here. Anything the traveler
    asks for happens; only their own hard rules (an avoid, a place they turned down) hold an add
    back until they insist (force). Events are the record of what changed, so a no-op says so.
    """
    events: list[str] = []
    blocked: list[dict] = []
    research: list[str] = []
    profile = copilot["profile"]
    current = copilot["current_day"]
    for op in ops[:10] if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        kind, name = op.get("op"), clean(op.get("name"))
        day_no = _day_arg(copilot, op.get("day"), current)
        hit = next(((d, i) for d, i in scheduled(copilot) if find([i], name)), None) if name else None
        if kind == "move" and name and not hit:
            kind = "add"  # "move X to day 3" for a place not planned yet means "put it there"

        if kind == "add" and name:
            item = find(copilot["pool"], name) or make_item({**op, "name": name})
            if find([i for _, i in scheduled(copilot)], item["name"]):
                events.append(f"{item['name']} is already in the plan")
                continue
            fit = check_add(copilot, day_no, item)
            impossible = [r for r in fit["reasons"] if r["code"] == "unreachable"]
            if impossible:
                blocked.append({"item": item, "day": day_no, "reasons": impossible, "cost_delta": 0})
                continue
            rules = [r for r in fit["reasons"] if r["code"] in ASK_FIRST]
            if rules and op.get("force") is not True:
                blocked.append({"item": item, "day": day_no, "reasons": rules, "cost_delta": 0})
                continue
            day_of(copilot, day_no)["items"].append(item)
            if item["name"] in profile["rejected"]:
                profile["rejected"].remove(item["name"])
            events.append(f"Added {item['name']} to day {day_no}"
                          + (" (you overrode a warning)" if rules else "") + _heads_up(copilot, day_no))
        elif kind == "remove" and name:
            if hit:
                hit[0]["items"].remove(hit[1])
                events.append(f"Removed {hit[1]['name']} from day {hit[0]['day']}")
                if op.get("reject") is True:
                    profile["rejected"].append(hit[1]["name"])
            elif op.get("reject") is True:
                profile["rejected"].append(name)
                events.append(f"Won't suggest {name} again")
            else:
                events.append(f"{name} wasn't in the plan")
        elif kind == "move" and name:
            source, item = hit
            if source["day"] == day_no:
                events.append(f"{item['name']} is already on day {day_no}")
                continue
            impossible = [r for r in check_add(copilot, day_no, item)["reasons"] if r["code"] == "unreachable"]
            if impossible:
                blocked.append({"item": item, "day": day_no, "reasons": impossible, "cost_delta": 0})
                continue
            before = budget_status(copilot)["committed"]
            source["items"].remove(item)
            day_of(copilot, day_no)["items"].append(item)
            saved = round(before - budget_status(copilot)["committed"], 2)
            events.append(f"Moved {item['name']} from day {source['day']} to day {day_no}"
                          + (f", saving ~{saved:g} {copilot['currency']}" if saved > 0 else "")
                          + _heads_up(copilot, day_no))
        elif kind == "set_budget":
            amount = number(op.get("amount"))
            currency = str(op.get("currency") or "").upper()
            if currency in CURRENCIES and currency != copilot["currency"]:
                if any(i["est_cost"] for i in copilot["pool"]) or scheduled(copilot):
                    events.append(f"Kept estimates in {copilot['currency']}; I don't convert currencies silently")
                else:
                    copilot["currency"] = currency
            if amount:
                copilot["budget"] = amount
                events.append(f"Budget set to {amount:g} {copilot['currency']}")
        elif kind == "set_cost" and name:
            amount = number(op.get("amount"))
            if amount is None:
                continue
            if name in ("stay_per_night", "food_per_day"):
                copilot["rates"][name] = amount
            elif name in MODES:
                copilot["rates"]["transfer"][name] = amount
            else:
                item = find([i for _, i in scheduled(copilot)] + copilot["pool"], name)
                if not item:
                    continue
                item["est_cost"], item["basis"] = amount, "traveler"
                name = item["name"]
            events.append(f"Cost for {name} set to {amount:g} {copilot['currency']}")
        elif kind in ("pref", "unpref") and op.get("kind") in PREF_LISTS:
            value = clean(op.get("value"), 80)
            if not value:
                continue
            group, field = PREF_LISTS[op["kind"]]
            target = profile[group][field]
            existing = next((t for t in target if key(t) == key(value)), None)
            if kind == "unpref":
                if existing:
                    target.remove(existing)
                    events.append(f"Dropped {op['kind']} preference: {value}")
                continue
            other_group, other_field = PREF_LISTS[OPPOSITE[op["kind"]]]
            profile[other_group][other_field][:] = [t for t in profile[other_group][other_field] if key(t) != key(value)]
            if not existing:
                target.append(value)
                events.append(PREF_SAVED[op["kind"]].format(value))
            if op["kind"] == "avoid":
                clashes = [i["name"] for _, i in scheduled(copilot) if matches(value, i)]
                if clashes:
                    events.append(f"Already planned but clashes with '{value}': {', '.join(clashes)}")
            if op["kind"] == "must" and not find(copilot["pool"], value):
                research.append(f"{copilot['destination']} {value}")
        elif kind == "set_mode" and op.get("value") in MODES:
            profile["travel_mode"] = op["value"]
            events.append(f"Travel mode set to {op['value']}")
        elif kind == "set_pace" and op.get("value") in PACE_HOURS:
            profile["pace"] = op["value"]
            events.append(f"Pace set to {op['value']}")
        elif kind == "set_vibe":
            values = op.get("value") if isinstance(op.get("value"), list) else [op.get("value")]
            profile["vibe"] = list(dict.fromkeys([*profile["vibe"], *(clean(v, 40) for v in values if clean(v, 40))]))[-5:]
            events.append(f"Vibe: {', '.join(profile['vibe'])}")
        elif kind == "set_base" and clean(op.get("base")):
            days = op.get("days") if isinstance(op.get("days"), list) else [day_no]
            changed = []
            for value in days:
                if type(value) is int and 1 <= value <= len(copilot["days"]):
                    target = day_of(copilot, value)
                    old = target["base"]
                    target["base"] = clean(op["base"])
                    route = route_reality.day_travel(copilot, target["base"], target["items"],
                                                     profile["travel_mode"], HOP_MINUTES[profile["travel_mode"]])
                    if route["known_hours"] >= PACE_HOURS[profile["pace"]]:
                        target["base"] = old
                        events.append(f"Kept day {value} in {old}; travel from {clean(op['base'])} would take about {route['hours']:g} h")
                    else:
                        changed.append(value)
            if changed:
                events.append(f"Base for day(s) {', '.join(map(str, changed))}: {clean(op['base'])}")
        elif kind in ("goto_day", "next_day"):
            copilot["current_day"] = current = (min(current + 1, len(copilot["days"])) if kind == "next_day"
                                                else _day_arg(copilot, op.get("day"), current))
        elif kind == "research" and clean(op.get("query")):
            research.append(clean(op["query"]))
        elif kind == "finish":
            copilot["status"] = "complete"
            events.append("Marked the itinerary complete")
    return events, blocked, research


# --------------------------------------------------------------------------
# Rendering for the map and budget ledger
# --------------------------------------------------------------------------

def to_markdown(copilot: dict) -> str:
    lines = ["| Day | Base | Plan |", "|---|---|---|"]
    for day in copilot["days"]:
        plan = ", ".join(f"**{item['name']}**" + (f" ({item['area']})" if item.get("area") else "")
                         for item in day["items"]) or "Open"
        lines.append(f"| Day {day['day']} | {day['base']} | {plan} |")
    return "\n".join(lines)


def to_extracted(copilot: dict, origin: str | None) -> dict:
    """Shape copilot days like the itinerary extractor's output so the shared graph builder can use it."""
    stops: list[dict] = []
    for day in copilot["days"]:
        if stops and key(stops[-1]["name"]) == key(day["base"]):
            stops[-1]["day_end"] = day["day"]
        else:
            stops.append({"name": day["base"], "day_start": day["day"], "day_end": day["day"], "nearby_places": []})
        stops[-1]["nearby_places"] += [{"name": item["name"],
                                         "category": "restaurant" if item["category"] in ("food", "restaurant") else "attraction"}
                                        for item in day["items"]]
    order = [stop["name"] for stop in stops]
    return {"stops": stops, "route_order": [origin, *order, origin] if origin else order, "legs": []}
