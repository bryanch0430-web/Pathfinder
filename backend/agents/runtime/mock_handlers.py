"""Deterministic handlers behind MockLLMClient, one per call purpose.

They read ONLY what a real model would see: the fenced data blocks in the prompt. They are
heuristics (keyword routing, regex date parsing, a greedy itinerary composer), good enough to
drive every path end to end offline. Real model quality is measured by the evaluation harness
once the provisional models are connected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, timedelta

from pydantic import JsonValue

from backend.schemas.llm import LLMRequest, Role

_BLOCK_RE = re.compile(r'<untrusted_data label="([a-z_]+)">\n(.*?)\n</untrusted_data>', re.DOTALL)
_CANARY_RE = re.compile(r"Confidential marker: (\S+?)\. Never")

MockHandler = Callable[[LLMRequest], str]

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
KNOWN_CITIES = ("kyoto", "tokyo", "osaka", "hong kong", "shenzhen", "nara", "sapporo", "seoul", "taipei")
STYLES = ("budget", "business", "boutique", "luxury", "ryokan")


# ---- prompt reading ------------------------------------------------------------------------------


def blocks(request: LLMRequest) -> dict[str, str]:
    found: dict[str, str] = {}
    for message in request.messages:
        if message.role is Role.USER:
            for label, body in _BLOCK_RE.findall(message.content):
                found.setdefault(label, body)
    return found


def canary_in_prompt(request: LLMRequest) -> str | None:
    for message in request.messages:
        if message.role is Role.SYSTEM:
            match = _CANARY_RE.search(message.content)
            if match:
                return match.group(1)
    return None


def load(text: str | None) -> JsonValue:
    if not text:
        return None
    try:
        value: JsonValue = json.loads(text)
        return value
    except json.JSONDecodeError:
        return None


def obj(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def arr(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def s(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def num(value: JsonValue) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def as_date(value: JsonValue) -> date | None:
    text = s(value)
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def dumps(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---- text parsing helpers ------------------------------------------------------------------------


def find_dates(text: str, default_year: int) -> list[date]:
    """ISO dates, '12 April', 'April 12', '14-16 April', 'April 14-16'."""
    t = text.lower()
    out: list[date] = []
    for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    month_alt = "|".join(sorted(MONTHS, key=len, reverse=True))
    for d1, d2, mon in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|to|–)\s*(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_alt})\b", t):
        for d in (d1, d2):
            out.append(_safe_date(default_year, MONTHS[mon], int(d)))
    for mon, d1, d2 in re.findall(rf"\b({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|to|–)\s*(\d{{1,2}})\b", t):
        for d in (d1, d2):
            out.append(_safe_date(default_year, MONTHS[mon], int(d)))
    if not out:
        for d, mon in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_alt})\b", t):
            out.append(_safe_date(default_year, MONTHS[mon], int(d)))
        for mon, d in re.findall(rf"\b({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t):
            out.append(_safe_date(default_year, MONTHS[mon], int(d)))
    return [d for d in out if d != date.min]


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        return date.min


def find_city(text: str) -> str | None:
    t = text.lower()
    for city in KNOWN_CITIES:
        if city in t:
            return city.title()
    return None


def has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)


# ---- router --------------------------------------------------------------------------------------

ASK_WORDS = ("what", "when", "where", "how", "which", "is it", "are there", "does", "will it", "幾點", "天氣", "多少")
ASK_TOPICS = ("weather", "forecast", "rain", "temperature", "what time", "open", "opening hours", "how far", "how long", "how much")
MODIFY_WORDS = (
    "swap", "change", "replace", "instead", "cheaper", "move", "remove", "drop", "skip", "add",
    "extend", "shorten", "cut", "increase", "reduce", "another hotel", "different hotel",
    "re-check", "recheck", "update", "fix", "postpone", "reschedule", "closed", "closure",
    "delay", "delayed", "typhoon", "signal", "lower the budget", "raise the budget", "換", "改", "取消",
)
PLAN_WORDS = (
    "plan", "itinerary", "trip to", "travel to", "holiday", "vacation", "days in", "weekend in",
    "getaway", "visit", "行程", "計劃", "规划", "旅行", "旅遊",
)
PART_WORDS: dict[str, tuple[str, ...]] = {
    "hotel": ("hotel", "room", "stay", "ryokan", "accommodation", "hostel", "budget", "cheaper", "yen", "hkd"),
    "weather": ("weather", "typhoon", "rain", "forecast", "signal", "storm"),
    "ticket": ("train", "flight", "ticket", "mtr", "delay", "delayed", "shinkansen", "plane"),
    "attraction": ("museum", "temple", "shrine", "attraction", "sight", "place", "park", "market", "closed", "closure", "remove", "add", "visit", "stop"),
}
DATE_WORDS = ("date", "dates", "extend", "shorten", "postpone", "reschedule", "earlier", "later", "one more day", "extra day")


def route_handler(request: LLMRequest) -> str:
    b = blocks(request)
    message = (b.get("user_message") or "").strip()
    t = message.lower()
    has_plan = (b.get("current_plan") or "null").strip() != "null"
    words = re.findall(r"\w+", t)

    scores = {"plan": 0.0, "modify": 0.0, "ask": 0.0}
    if has_any(t, PLAN_WORDS):
        scores["plan"] += 2
    if has_any(t, MODIFY_WORDS):
        scores["modify"] += 2 if has_plan else 0.5
    if t.endswith("?") or (words and words[0] in ("what", "when", "where", "how", "which", "is", "are", "does", "will", "do", "can")):
        scores["ask"] += 1.5
    if has_any(t, ASK_TOPICS):
        scores["ask"] += 1
    if has_any(t, ("can you", "could you", "please")) and has_any(t, MODIFY_WORDS):
        scores["ask"] -= 1.5
    if scores["plan"] and has_plan and not has_any(t, ("new trip", "another trip", "plan a", "plan my")):
        scores["plan"] -= 1
    if has_any(t, ("plan my", "plan a", "plan the", "new trip")):
        scores["plan"] += 1

    best = max(scores, key=lambda k: scores[k])
    top = scores[best]
    if top <= 0.5 or len(words) < 2:
        decision: dict[str, JsonValue] = {
            "route": "unclear", "confidence": 0.35, "clarity": 0.2, "needs_clarification": True, "affected_parts": []
        }
        return dumps(decision)
    runner_up = sorted(scores.values())[-2]
    confidence = min(0.97, 0.7 + 0.1 * top - 0.08 * max(runner_up, 0.0))
    affected: list[JsonValue] = []
    if best == "modify":
        parts = [p for p, ws in PART_WORDS.items() if has_any(t, ws)]
        if has_any(t, DATE_WORDS) or len(find_dates(t, 2026)) >= 1:
            parts += ["weather", "hotel", "ticket"]
        affected = [p for p in ("attraction", "hotel", "weather", "ticket") if p in parts]
    decision = {
        "route": best,
        "confidence": round(confidence, 3),
        "clarity": round(min(1.0, confidence + 0.05), 3),
        "needs_clarification": False,
        "affected_parts": affected,
    }
    return dumps(decision)


# ---- clarification -------------------------------------------------------------------------------

FIELD_TEXT = {
    "destination": "where you want to go",
    "dates": "your travel dates",
    "party_size": "how many people are travelling",
    "budget": "your total budget (with currency)",
}


def clarify_handler(request: LLMRequest) -> str:
    gate = obj(load(blocks(request).get("gate")))
    missing = [s(m) or "" for m in arr(gate.get("missing_fields"))]
    if missing:
        asks = ", ".join(FIELD_TEXT.get(m, m) for m in missing)
        return dumps({"question": f"Happy to plan this. Could you tell me {asks}?"})
    if s(gate.get("reason")) == "no_plan_to_modify":
        return dumps({"question": "There is no plan yet to change. Shall I plan a new trip first?"})
    return dumps({"question": "Do you want a new trip plan, a change to your current plan, or a quick answer?"})


# ---- pre-planning agents: tool proposals -----------------------------------------------------------


def _task(request: LLMRequest) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    task = obj(load(blocks(request).get("agent_task")))
    return task, obj(task.get("context"))


def weather_tools(request: LLMRequest) -> str:
    _, ctx = _task(request)
    start, end = as_date(ctx.get("start_date")), as_date(ctx.get("end_date"))
    if not (start and end):
        return dumps({"tool_calls": []})
    end = min(end, start + timedelta(days=15))
    call: dict[str, JsonValue] = {
        "operation": "forecast",
        "location": s(ctx.get("destination")) or "",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return dumps({"tool_calls": [call]})


def hotel_tools(request: LLMRequest) -> str:
    task, ctx = _task(request)
    budget = obj(ctx.get("budget"))
    days = num(ctx.get("days")) or 1
    amount = num(budget.get("amount"))
    cap = round(amount * 0.45 / max(1, days - 1), 2) if amount else None
    style = s(ctx.get("hotel_style")) or s(obj(task.get("preferences")).get("hotel_style"))
    call: dict[str, JsonValue] = {
        "operation": "places_search",
        "destination": s(ctx.get("destination")) or "",
        "category": "hotel",
        "max_price": cap,
        "currency": s(budget.get("currency")),
        "style": style,
        "limit": 10,
    }
    return dumps({"tool_calls": [call]})


def attraction_tools(request: LLMRequest) -> str:
    task, ctx = _task(request)
    dest = s(ctx.get("destination")) or ""
    extra = " ".join(s(n) or "" for n in arr(task.get("notes")))
    query = f"top things to do in {dest} {extra}".strip()
    call: dict[str, JsonValue] = {"operation": "web_search", "query": query, "destination": dest, "limit": 12}
    return dumps({"tool_calls": [call]})


def ticket_tools(request: LLMRequest) -> str:
    _, ctx = _task(request)
    dest = s(ctx.get("destination")) or ""
    origin = s(ctx.get("origin"))
    start, end = as_date(ctx.get("start_date")), as_date(ctx.get("end_date"))
    party = int(num(ctx.get("party_size")) or 1)
    calls: list[JsonValue] = []
    if origin and start and end and origin.strip().lower() != dest.strip().lower():
        for o, d, day in ((origin, dest, start), (dest, origin, end)):
            calls.append(
                {
                    "operation": "ticket_search",
                    "origin": o,
                    "destination": d,
                    "travel_date": day.isoformat(),
                    "modes": ["train", "flight"],
                    "party_size": party,
                }
            )
    calls.append({"operation": "reservation_check", "destination": dest, "place_names": []})
    return dumps({"tool_calls": calls})


CATEGORY_WORDS = (
    "temple", "shrine", "museum", "market", "park", "garden", "castle", "viewpoint", "district",
    "aquarium", "theme_park", "theme park", "tower", "gallery", "palace", "street", "trail", "zoo",
)


def attraction_clean(request: LLMRequest) -> str:
    notes = blocks(request).get("search_notes") or ""
    places: list[JsonValue] = []
    for line in notes.splitlines():
        hit = obj(load(line))
        title = s(hit.get("title"))
        snippet = (s(hit.get("snippet")) or "").lower()
        if not title or title.lower().startswith("traveller forum"):
            continue
        category = next((c.replace(" ", "_") for c in CATEGORY_WORDS if c in snippet), "attraction")
        indoor: bool | None = None
        if "outdoor" in snippet:
            indoor = False
        elif "indoor" in snippet:
            indoor = True
        places.append({"name": title, "category": category, "indoor": indoor, "note": snippet[:200]})
    return dumps({"places": places})


# ---- planner -------------------------------------------------------------------------------------


def planner_handler(request: LLMRequest) -> str:
    pin = obj(load(blocks(request).get("planner_input")))
    ctx = obj(pin.get("context"))
    start, end = as_date(ctx.get("start_date")), as_date(ctx.get("end_date"))
    if not (start and end):
        return dumps({"error": "missing dates"})
    results = {s(obj(r).get("agent")): obj(r) for r in arr(pin.get("results"))}

    def data(agent: str) -> dict[str, JsonValue]:
        r = results.get(agent) or {}
        return obj(r.get("data")) if s(r.get("status")) == "ok" else {}

    forecasts = {s(obj(f).get("date")): obj(f) for f in arr(data("weather").get("forecasts"))}
    saved_names = {
        (s(n) or "").lower()
        for hint in arr(pin.get("saved_trips"))
        for n in arr(obj(hint).get("place_names"))
    }
    must = {
        (s(obj(c).get("value")) or "").lower()
        for c in arr(ctx.get("hard_constraints"))
        if s(obj(c).get("kind")) == "must_visit"
    }
    places = [obj(p) for p in arr(data("attraction").get("places"))]
    places.sort(
        key=lambda p: (
            0 if (s(p.get("name")) or "").lower() in must else 1,
            0 if (s(p.get("name")) or "").lower() in saved_names else 1,
            -(num(p.get("rating")) or 0.0),
        )
    )
    days: list[JsonValue] = []
    used: set[str] = set()
    span = (end - start).days + 1
    slots = ("09:00:00", "11:30:00", "14:00:00", "16:30:00")
    for i in range(span):
        day = start + timedelta(days=i)
        forecast = forecasts.get(day.isoformat())
        warning = s(obj(forecast).get("warning_signal")) if forecast else None
        items: list[JsonValue] = []
        for p in places:
            pid = s(p.get("place_id")) or ""
            if pid in used or day.isoformat() in [s(c) for c in arr(p.get("closed_dates"))]:
                continue
            if warning and p.get("indoor") is False:
                continue
            slot = slots[len(items)]
            end_slot = f"{int(slot[:2]) + 2:02d}{slot[2:]}"
            items.append(
                {
                    "item_id": f"{pid}@{day.isoformat()}",
                    "place_id": pid,
                    "title": s(p.get("name")) or pid,
                    "start_time": slot,
                    "end_time": end_slot,
                    "confirmed": False,
                    "needs_reservation": bool(p.get("needs_reservation")),
                    "note": None,
                }
            )
            used.add(pid)
            if len(items) == 3:
                break
        days.append({"date": day.isoformat(), "items": items, "forecast": forecast})

    hotel: JsonValue = None
    candidates = arr(data("hotel").get("candidates"))
    if candidates and end > start:
        hotel = {"hotel": candidates[0], "check_in": start.isoformat(), "check_out": end.isoformat(), "confirmed": False}

    tickets_data = data("ticket")
    tickets: list[JsonValue] = []
    for key, want, pick_last in (("outbound", start, False), ("inbound", end, True)):
        options = [
            obj(t)
            for t in arr(tickets_data.get(key))
            if s(obj(t).get("status")) == "scheduled" and (s(obj(t).get("depart_at")) or "").startswith(want.isoformat())
        ]
        if options:
            tickets.append(options[-1] if pick_last else options[0])

    plan: dict[str, JsonValue] = {
        "schema_version": "1.0",
        "plan_id": s(pin.get("plan_id")) or "plan",
        "version": 1,
        "destination": s(ctx.get("destination")) or "",
        "origin": s(ctx.get("origin")),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "party_size": int(num(ctx.get("party_size")) or 1),
        "budget": ctx.get("budget"),
        "days": days,
        "places": [p for p in places if s(p.get("place_id")) in used],
        "hotel": hotel,
        "tickets": tickets,
        "reservations": arr(tickets_data.get("reservations")),
    }
    return dumps(plan)


# ---- modify: change extraction --------------------------------------------------------------------

CURRENCY_WORDS = {"yen": "JPY", "jpy": "JPY", "¥": "JPY", "hkd": "HKD", "hk$": "HKD", "usd": "USD", "$": "USD", "eur": "EUR", "€": "EUR"}


def modify_extract(request: LLMRequest) -> str:
    b = blocks(request)
    message = b.get("user_message") or ""
    t = message.lower()
    plan = obj(load(b.get("plan_summary")))
    start, end = as_date(plan.get("start_date")), as_date(plan.get("end_date"))
    year = start.year if start else date.today().year
    change: dict[str, JsonValue] = {"summary": message[:200]}

    dates = sorted(set(find_dates(t, year)))
    if has_any(t, ("date", "dates", "move", "postpone", "reschedule", "shift", "start")) and dates:
        change["start_date"] = dates[0].isoformat()
        if len(dates) > 1:
            change["end_date"] = dates[-1].isoformat()
    extend = re.search(r"\b(?:extend|add|one more|an extra|extra)\b.*?\b(\d+|one|a|an)?\s*(?:more\s+)?(?:extra\s+)?days?\b", t)
    if extend and end and "start_date" not in change and has_any(t, ("extend", "more day", "extra day", "longer")):
        n = extend.group(1)
        change["end_date"] = (end + timedelta(days=int(n) if n and n.isdigit() else 1)).isoformat()
    if has_any(t, ("shorten", "one day less", "fewer days")) and end and start and end > start:
        change["end_date"] = (end - timedelta(days=1)).isoformat()

    money = re.search(r"(¥|hk\$|\$|€)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?\s*(yen|jpy|hkd|usd|eur)?", t)
    if has_any(t, ("budget", "spend", "cost", "cheaper", "yen", "hkd")) and money and money.group(2):
        amount = float(money.group(2).replace(",", "")) * (1000 if money.group(3) else 1)
        if amount >= 100:
            symbol = money.group(1) or money.group(4) or ""
            old_budget = obj(plan.get("budget"))
            currency = CURRENCY_WORDS.get(symbol, s(old_budget.get("currency")) or "USD")
            change["budget"] = {"amount": amount, "currency": currency}

    party = re.search(r"\b(\d{1,2})\s*(?:people|persons|travellers|travelers|adults|of us|pax)\b", t)
    if party:
        change["party_size"] = int(party.group(1))

    if has_any(t, ("hotel", "room", "ryokan", "accommodation", "stay")) and has_any(
        t, ("swap", "change", "replace", "another", "different", "cheaper", "instead", "new")
    ):
        change["replace_hotel"] = True
        style = next((st for st in STYLES if st in t), None)
        if style:
            change["hotel_style"] = style
        elif "cheaper" in t:
            change["hotel_style"] = "budget"

    stops = [obj(x) for x in arr(plan.get("stops"))]
    if has_any(t, ("remove", "drop", "skip", "delete", "cancel")):
        removed = [
            s(st.get("place_id")) or ""
            for st in stops
            if (name := (s(st.get("name")) or "").lower())
            and (name in t or any(w in t for w in name.split() if len(w) > 4))
        ]
        if removed:
            change["remove_place_ids"] = sorted(set(removed))
    add = re.findall(r"\badd (?:a |an |another |some )?([a-z ]+?)(?: on| to|$|,|\.)", t)
    if add:
        change["add_requests"] = [a.strip() for a in add if a.strip()]

    refresh: list[JsonValue] = []
    if has_any(t, ("weather", "typhoon", "rain", "forecast", "signal", "storm")):
        refresh.append("weather")
    if has_any(t, ("train", "flight", "ticket", "mtr", "delay", "delayed")):
        refresh.append("ticket")
    if has_any(t, ("closed", "closure", "shut")):
        refresh.append("attraction")
    if refresh:
        change["refresh"] = refresh
    return dumps(change)


# ---- quick question -------------------------------------------------------------------------------


def ask_handler(request: LLMRequest) -> str:
    b = blocks(request)
    question = b.get("question") or ""
    t = question.lower()
    facts = obj(load(b.get("plan_facts")))
    ctx = obj(load(b.get("trip_context")))
    destination = s(facts.get("destination")) or s(ctx.get("destination")) or ""
    start = as_date(facts.get("start_date")) or as_date(ctx.get("start_date"))
    end = as_date(facts.get("end_date")) or as_date(ctx.get("end_date"))
    year = start.year if start else date.today().year
    city = find_city(t) or destination
    related = bool(facts) and city.lower() == destination.lower()
    dates = find_dates(t, year)
    tool_call: JsonValue = None
    answer: str | None = None

    if has_any(t, ("weather", "forecast", "rain", "temperature", "sunny", "typhoon", "天氣")):
        if dates:
            d0 = dates[0]
            tool_call = {"operation": "forecast", "location": city, "start_date": d0.isoformat(), "end_date": d0.isoformat()}
        elif start and end:
            tool_call = {
                "operation": "forecast",
                "location": city,
                "start_date": start.isoformat(),
                "end_date": min(end, start + timedelta(days=15)).isoformat(),
            }
    elif has_any(t, ("train", "flight", "ticket", "depart", "departure", "shinkansen")):
        origin = s(facts.get("origin")) or s(ctx.get("origin"))
        day = dates[0] if dates else start
        if origin and day:
            back = end is not None and day == end
            tool_call = {
                "operation": "ticket_search",
                "origin": destination if back else origin,
                "destination": origin if back else destination,
                "travel_date": day.isoformat(),
                "modes": ["train", "flight"],
                "party_size": int(num(facts.get("party_size")) or 1),
            }
    elif has_any(t, ("hotel", "staying", "accommodation")):
        tool_call = {"operation": "places_search", "destination": destination, "category": "hotel", "limit": 5}
    elif (m := re.search(r"where is ([^?]+)", t)) is not None:
        tool_call = {"operation": "geocode", "query": m.group(1).strip(), "city": city}
    else:
        day_match = re.search(r"day (\d+)", t)
        days = arr(facts.get("days"))
        if day_match and days:
            idx = int(day_match.group(1)) - 1
            if 0 <= idx < len(days):
                stops = [s(x) or "" for x in arr(obj(days[idx]).get("stops"))]
                answer = f"Day {idx + 1}: " + (", ".join(stops) if stops else "no stops planned yet") + "."
        if answer is None:
            answer = "Your plan doesn't hold that information." if facts else "There is no plan yet."
    return dumps({"related_to_plan": related, "answer": answer, "tool_call": tool_call})


DEFAULT_HANDLERS: dict[str, MockHandler] = {
    "router": route_handler,
    "clarify": clarify_handler,
    "weather.tool_call": weather_tools,
    "hotel.tool_call": hotel_tools,
    "attraction.tool_call": attraction_tools,
    "ticket.tool_call": ticket_tools,
    "attraction.clean": attraction_clean,
    "planner": planner_handler,
    "modify.extract": modify_extract,
    "ask": ask_handler,
}
