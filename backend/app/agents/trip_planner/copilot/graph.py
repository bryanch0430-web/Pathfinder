"""Build-with-agent subgraph: plan the trip one day at a time with the traveler.

    START ─┬─ init ───────────────────────────┐
           └─ interpret → apply ─┬─ research ─┤
                                 └────────────┴→ recommend → respond → render → END

interpret (fast model) turns the message into ops; apply/recommend/render are
deterministic (engine.py); research pulls traveler posts; respond (main model)
only puts the computed facts into words.
"""

import copy
import json
import logging
import re
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.trip_planner.copilot import engine
from app.agents.trip_planner.copilot.research import initial_queries, research
from app.agents.trip_planner.nodes.extract_itinerary_node import build_itinerary_graph
from app.services.enrichment import agent_facts, get_enrichment
from app.services import route_reality
from app.services.llm.service import llm_service

logger = logging.getLogger(__name__)

START_ACTION = "START_BUILD_WITH_AGENT"
OPS_ACTION = "COPILOT_OPS"


class CopilotState(TypedDict, total=False):
    copilot: dict
    user_message: str
    ui_action: dict | None
    destination: str
    duration_days: int
    origin: str | None
    currency: str | None
    budget_target: float | None
    planning_preferences: dict
    places_to_visit: list[str]
    user_name: str | None
    recent: list[dict]  # the last exchange, [{"role": "traveler" | "you", "text"}], oldest first

    ops: list
    events: list[str]
    blocked: list[dict]
    queries: list[str]
    plan_bases: bool
    focus_day: int | None
    nav_only: bool
    route_turn_start: int
    defer_reply: bool
    facts: dict
    assistant_response: str
    itinerary_graph: dict


INTERPRET_INSTRUCTION = """You convert a traveler's message into edit operations for a day-by-day itinerary you are building together.
Return JSON only: {"ops":[...]}. Use {"ops":[]} when the message is only a question or chat.
Operations:
{"op":"add","name":"place","day":2,"force":false,"est_cost":null,"duration_hours":null,"area":null,"category":null,"tags":[]}
   force=true only when they insist after a warning ("add it anyway"). For places not in POOL give your best
   per-person est_cost in CURRENCY, duration_hours, area, category, tags.
{"op":"remove","name":"...","reject":true}      reject=true when they never want it suggested again
{"op":"move","name":"...","day":3}
{"op":"set_budget","amount":12000,"currency":"JPY"}
{"op":"set_cost","name":"place|stay_per_night|food_per_day|walk|transit|taxi|drive","amount":1500}
{"op":"pref","kind":"must|avoid|like|dislike","value":"short term"}
   must/avoid are hard rules ("must", "never", "can't", allergies, mobility); like/dislike are soft leanings ("prefer", "not a fan").
{"op":"unpref","kind":"must|avoid|like|dislike","value":"..."}
{"op":"set_mode","value":"walk|transit|taxi|drive"}
{"op":"set_pace","value":"relaxed|balanced|packed"}
{"op":"set_vibe","value":["chill","foodie"]}
{"op":"set_base","days":[3,4],"base":"Kyoto"}
{"op":"next_day"}  {"op":"goto_day","day":4}
{"op":"research","query":"late-night ramen near Shinjuku"}   when they want ideas POOL cannot answer
{"op":"finish"}   when they say it's final, done, lock it in, or to finalize the plan
"The 2nd one" refers to LAST SUGGESTIONS. "day" defaults to CURRENT DAY. Use exact names from POOL or PLAN.
Resolve "it", "that", "there", "the first option" against RECENT CHAT, newest first: in "move it to day 5" after
your reply offered Club Cubana, "it" is Club Cubana. A yes ("yes", "do it", "go ahead", "sounds good") to a change
you proposed in your last reply means the ops for exactly that proposal. "Anyway"/"do it anyway" after a warning
means the same op with "force":true.
Any request to change the plan must produce ops: never answer a change with {"ops":[]}. A move of a place not in
PLAN is a move op too (it puts the place on that day). "Swap A and B" is two moves.
Emit the op they asked for even when it breaks a rule in PROFILE, goes over budget or overfills the day: the planner
checks all of that and asks them itself. Never drop, soften or replace a request on your own.
Only "add" a real, specifically named place (from POOL, PLAN, LAST SUGGESTIONS, RECENT CHAT, or named by the traveler).
A vague wish ("in the mood for good food and a quiet garden") is never an add: record it as {"op":"pref","kind":"like"}
and add {"op":"research","query":"<destination/base> <wish> recommendations"}.
"""

RESPOND_INSTRUCTION = """You are Pathfinder, a friendly, travel-savvy planner chatting with the traveler while you build
their trip together, one day at a time. Write like a person messaging a friend: warm, natural and short
(usually 2-5 sentences, at most ~120 words). Plain Markdown: no headings and no tables; a short bullet list
only when offering 2-3 options.

FACTS come from the planner and are your only source of truth:
- Name only places that appear in FACTS. Use only numbers from FACTS, with the currency and "~" (estimates).
- If the trip's origin or destination is not on Earth, say that once, plainly, and keep planning it as a proof of concept. Do not refuse.
- events is the complete list of what just changed, and it is already saved to their Trip Studio. Open with
  those changes in plain words ("Kagurazaka is on day 1 now."). Never say something was done, added, moved or
  removed unless events says so. If they asked for a change and events has nothing for it, say plainly that
  nothing changed and why (see blocked), or ask what they meant. Don't ask them to confirm or finalize a change
  that is in events. If they asked a question, answer it before anything else. When they asked for a kind of
  place ("a lunch spot") and events added one, name that one: every other place you mention is an option they
  can pick, never something you did ("Omide Yokocho is another good lunch option", not "I've added Omide
  Yokocho").
- An event with a "heads-up" went through but strains the day or the budget: mention it in a few words and offer
  one fix (move something to a lighter day, drop something).
- If a place cannot fit because travel alone is too long, say the approximate one-way and day travel time in plain
  words and suggest another day, a closer base, or an overnight stay. Do not offer "add anyway" for that case.
  A conflict with the traveler's own no-X rule can still be overridden after warning.
- Totals come from FACTS only: the trip total is budget.committed and a day's total is its cost. Never add
  numbers up yourself.
- If offer_lock_budget is true, offer once to lock the completed budget in one short sentence. The traveler
  can use the button or say "lock the budget". If budget.is_locked is true, describe it as final.
- Mention a money_moves entry only when it saves something meaningful, as a friendly tip.
- The traveler can already see hours and totals in the day panel. Only bring up the budget when it changed a
  lot, is over, or they asked. If budget.baseline (stay + food) alone exceeds the budget, say so kindly and
  offer the fixes: raise the budget, or tell you their real hotel or food costs.
- Offer up to 3 of the day's suggestions, each with a few words from its "why". Credit travelers on
  Reddit/Quora/TripAdvisor only when source_url is set. If suggestions is empty, don't invent places: ask what
  they're in the mood for.
- First turn (no events and nothing planned yet): say in one line how this works (you'll suggest places for each
  day; they can tap a suggestion or just tell you what they like), then open day 1.
- conditions (when present): if the current day has weather notes or a public holiday, work it in as one short,
  practical clause (an umbrella, an indoor backup from the suggestions, booking ahead, possible closures or crowds).
  Call it a forecast only when the label is "Forecast"; otherwise say it's typical for that month. Mention a
  heads_up entry only when it affects what they're deciding. Never invent weather or holidays.
- When events includes "Marked the itinerary complete": a brief wrap-up, one line per day plus the trip total,
  then invite changes. After that, a finished trip is edited like any other: reply to the change, no wrap-up.
- End with one easy question. Never mention tools, data, JSON, the planner or other internal systems.
"""


def _json(text: str) -> dict:
    match = re.search(r"\{[\s\S]*\}", text or "")
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def init(state: CopilotState) -> dict:
    copilot = engine.new_copilot(
        destination=state["destination"], duration_days=state["duration_days"],
        currency=state.get("currency"), preferences=state.get("planning_preferences"),
        places=state.get("places_to_visit"), budget_target=state.get("budget_target"),
        overrides=state.get("ui_action"),
    )
    return {"copilot": copilot, "events": [], "blocked": [], "plan_bases": True,
            "queries": initial_queries(copilot)}


async def interpret(state: CopilotState) -> dict:
    action = state.get("ui_action") or {}
    if action.get("action") == OPS_ACTION:
        return {"ops": action.get("ops") or []}
    copilot = state["copilot"]
    prompt = "\n".join([
        f"CURRENT DAY: {copilot['current_day']} of {len(copilot['days'])}",
        f"CURRENCY: {copilot['currency']}",
        "PLAN: " + json.dumps([{"day": d["day"], "base": d["base"], "items": [i["name"] for i in d["items"]]}
                               for d in copilot["days"]], ensure_ascii=False),
        "LAST SUGGESTIONS: " + json.dumps(copilot["last_suggestions"], ensure_ascii=False),
        "LAST WARNINGS: " + json.dumps(copilot.get("last_blocked", []), ensure_ascii=False),
        "POOL: " + json.dumps([i["name"] for i in copilot["pool"][:40]], ensure_ascii=False),
        "PROFILE: " + json.dumps(copilot["profile"], ensure_ascii=False),
        "RECENT CHAT (oldest first):\n" + "\n".join(
            f"{turn['role'].upper()}: {turn['text']}" for turn in state.get("recent") or []),
        f'TRAVELER MESSAGE: "{state.get("user_message") or ""}"',
    ])
    try:
        # The main model: this is the one decision in the turn that has to be right.
        parsed = _json(await llm_service.generate(prompt=prompt, system_instruction=INTERPRET_INSTRUCTION,
                                                  temperature=0.0))
    except Exception as exc:
        logger.warning("Copilot interpret failed: %s", exc)
        parsed = {}
    return {"ops": parsed.get("ops") if isinstance(parsed.get("ops"), list) else []}


async def _hydrate_route(copilot: dict, day: dict, items: list[dict], *, per_turn: int = 3,
                         roads: bool = True) -> None:
    requests = route_reality.requests_for_day(copilot["destination"], day["base"], items)
    await route_reality.resolve_points(copilot, requests, per_turn=per_turn)
    for item in items:
        position = route_reality.item_point(copilot, item)
        if position:
            item["lat"], item["lon"] = position
    if roads and copilot["profile"]["travel_mode"] in ("taxi", "drive"):
        await route_reality.resolve_roads(copilot, route_reality.road_pairs_for_day(copilot, day["base"], items))


async def apply(state: CopilotState) -> dict:
    copilot = state["copilot"]
    ops = state.get("ops") or []
    roads_used = False
    route_turn_start = len(copilot.get("route_queries") or [])
    for op in ops[:3] if isinstance(ops, list) else []:
        if not isinstance(op, dict) or op.get("op") not in ("add", "move", "set_base"):
            continue
        day = engine.day_of(copilot, op.get("day") if type(op.get("day")) is int else copilot["current_day"])
        if op.get("op") == "set_base" and op.get("base"):
            await route_reality.resolve_points(copilot, route_reality.requests_for_day(
                copilot["destination"], str(op["base"]), []), per_turn=1)
            continue
        name = engine.clean(op.get("name"))
        if not name:
            continue
        item = engine.find(copilot["pool"], name) or engine.find([i for d in copilot["days"] for i in d["items"]], name)
        if item is None:
            item = op
        remaining = max(0, 6 - (len(copilot.get("route_queries") or []) - route_turn_start))
        await _hydrate_route(copilot, day, [*day["items"], item],
                             per_turn=min(3, remaining), roads=not roads_used)
        roads_used = True
        position = route_reality.item_point(copilot, item)
        if position:
            op["lat"], op["lon"] = position
    events, blocked, queries = engine.apply_ops(copilot, ops)
    # Switching days only changes focus: no research, and respond replays that day's held reply.
    nav_only = bool(ops) and all(isinstance(op, dict) and op.get("op") in ("goto_day", "next_day") for op in ops)
    day = engine.day_of(copilot, copilot["current_day"])
    if not nav_only and copilot["status"] == "building" and len(engine.rank(copilot, day["day"])) < 2:
        likes = " ".join((copilot["profile"]["soft"]["likes"] + copilot["profile"]["vibe"])[:2])
        queries.append(f"{day['base']} {likes} recommendations local tips".replace("  ", " "))
    return {"copilot": copilot, "events": events, "blocked": blocked, "queries": queries,
            "route_turn_start": route_turn_start,
            "plan_bases": False, "nav_only": nav_only}


async def research_node(state: CopilotState) -> dict:
    copilot = state["copilot"]
    await research(copilot, state.get("queries") or [], plan_bases=bool(state.get("plan_bases")))
    return {"copilot": copilot}


def _brief(item: dict) -> dict:
    return {"name": item["name"], "area": item.get("area"), "category": item["category"],
            "est_cost": item["est_cost"], "duration_hours": item["duration_hours"],
            "why": item.get("why"), "source_url": item.get("source"), "cost_basis": item["basis"]}


async def _conditions(state: CopilotState, copilot: dict) -> dict | None:
    """Weather and holidays for the plan's dates (cached server-side, so usually instant)."""
    if state.get("nav_only"):
        return None  # a pure day switch replays the held reply
    try:
        start = date.fromisoformat(str((state.get("planning_preferences") or {}).get("start_date")))
    except ValueError:
        return None  # no dates, so no forecast or holidays; the exchange rate isn't a planning fact
    days = [(d["day"], d["base"], [(i["name"], " ".join(filter(None, (i["name"], i.get("category"), i.get("area")))))
                                   for i in d["items"]]) for d in copilot["days"]]
    enrichment = await get_enrichment(copilot["destination"], start, days, copilot["currency"], timeout=8.0)
    return agent_facts(enrichment, copilot["current_day"])


async def recommend(state: CopilotState) -> dict:
    copilot = state["copilot"]
    day_no = copilot["current_day"]
    conditions = await _conditions(state, copilot)
    day = engine.day_of(copilot, day_no)
    nearby_candidates = engine.rank(copilot, day_no, 2)
    road_calls_used = any(isinstance(op, dict) and op.get("op") in ("add", "move")
                          for op in state.get("ops") or [])
    remaining = max(0, 6 - (len(copilot.get("route_queries") or []) -
                            state.get("route_turn_start", len(copilot.get("route_queries") or []))))
    await _hydrate_route(copilot, day, [*day["items"], *nearby_candidates],
                         per_turn=min(3, remaining), roads=not road_calls_used)
    blocked = [engine.alternatives_for(copilot, b) for b in state.get("blocked") or []]
    suggestions = engine.rank(copilot, day_no, 3)
    status = engine.budget_status(copilot)
    copilot["last_suggestions"] = [s["name"] for s in suggestions]
    copilot["last_blocked"] = [{"name": b["item"]["name"], "day": b["day"]} for b in blocked]
    copilot["summary"] = status  # read-only snapshot for the client; recomputed every turn
    # Every day keeps its own ranked options so the client can switch days without a round trip.
    for other in copilot["days"]:
        other["suggestions"] = [s["name"] for s in engine.rank(copilot, other["day"], 3)]
    today = next(d for d in status["by_day"] if d["day"] == day_no)
    return {"copilot": copilot, "facts": {
        "user_message": state.get("user_message") or "",
        "status": copilot["status"],
        "total_days": len(copilot["days"]),
        "current_day": {**today, "items": [_brief(i) for i in day["items"]]},
        "events": state.get("events") or [],
        "blocked": [{"name": b["item"]["name"], "day": b["day"], "reasons": b["reasons"],
                     "cost": b["item"]["est_cost"], "alternatives": [_brief(a) for a in b["alternatives"]],
                     "fits_on_days": b["fits_on_days"]} for b in blocked],
        "suggestions": [_brief(s) for s in suggestions],
        "money_moves": engine.move_suggestions(copilot),
        "budget": {k: v for k, v in status.items() if k != "by_day"},
        "travel_mode": copilot["profile"]["travel_mode"],
        "preferences": copilot["profile"],
        "days_overview": [{"day": d["day"], "base": d["base"], "cost": d["cost"],
                           "items": [i["name"] for i in engine.day_of(copilot, d["day"])["items"]]}
                          for d in status["by_day"]],
        **({"conditions": conditions} if conditions else {}),
    }}


def _fallback_reply(facts: dict) -> str:
    """Plain reply built from the same facts when the model is unavailable."""
    budget, day = facts["budget"], facts["current_day"]
    lines = [f"- {event}" for event in facts["events"]]
    for b in facts["blocked"]:
        why = ", ".join(_reason_text(r) for r in b["reasons"])
        options = ", ".join(a["name"] for a in b["alternatives"])
        lines.append(f"- Couldn't add **{b['name']}** to day {b['day']}: {why}. "
                     + (f"It may fit on day {', '.join(map(str, b['fits_on_days']))}." if b["fits_on_days"] else
                        f"{('Closer options: ' + options + '.') if options else 'Try a closer base or an overnight stop.'}"))
    for move in facts["money_moves"][:1]:
        lines.append(f"- Tip: move **{move['name']}** from day {move['from_day']} to day {move['to_day']} "
                     f"to save ~{move['saves']:g} {budget['currency']} and {move['saves_minutes']} min.")
    planned = ", ".join(i["name"] for i in day["items"]) or "nothing yet"
    lines.append(f"\n**Day {day['day']} · {day['base']}** — {planned} ({day['hours']}/{day['capacity_hours']} h).")
    lines += [f"{n}. **{s['name']}**" + (f" (~{s['est_cost']:g} {budget['currency']})" if s["est_cost"] is not None else "")
              + (f" — {s['why']}" if s["why"] else "") for n, s in enumerate(facts["suggestions"], 1)]
    if budget["budget"] is not None:
        lines.append(f"\nBudget: ~{budget['committed']:g} of {budget['budget']:g} {budget['currency']} "
                     f"(~{budget['remaining']:g} left).")
    lines.append("\nWhich one should I add, or shall we move to the next day?")
    return "\n".join(lines).strip()


def reply_prompt(facts: dict) -> str:
    return "FACTS:\n" + json.dumps(facts, ensure_ascii=False, default=str)


# A clause that says the plan changed ("I've added X", "X is now on day 2", "I'll move X")...
_CLAIM = re.compile(r"\b(added|moved|removed|swapped|dropped|replaced|scheduled|booked|slotted|penciled|pencilled|"
                    r"put|(is|are) now (on|in|part of)|now (sits|lives)|I'?ll (add|move|remove|put|swap|drop)|"
                    r"I will (add|move|remove|put|swap|drop))\b", re.I)
# ...unless it's an offer, a question or a denial ("want me to add X?", "I couldn't add X").
_NOT_A_CLAIM = re.compile(r"\?\s*$|\b(could|can|would|might|may|should|shall|want|how about|if you|not|never|"
                          r"cannot)\b|n't\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def _false_claim(sentence: str, facts: dict) -> bool:
    """True when a sentence says a place changed that the planner didn't change (events is the only record)."""
    changed = " ".join(map(str, facts.get("events") or [])).lower()
    names = {i["name"] for i in facts.get("suggestions") or []}
    for b in facts.get("blocked") or []:
        names |= {b["name"], *(a["name"] for a in b.get("alternatives") or [])}
    names |= {i["name"] for i in (facts.get("current_day") or {}).get("items") or []}
    for day in facts.get("days_overview") or []:
        names |= set(day.get("items") or [])
    for clause in re.split(r"[,;:()—–]| and | but ", sentence):
        if not _CLAIM.search(clause) or _NOT_A_CLAIM.search(clause):
            continue
        mentioned = [n for n in names if n and n.lower() in clause.lower()]
        # A named place that isn't in events, or any change claimed on a turn that changed nothing.
        if any(n.lower() not in changed for n in mentioned) or not changed:
            return True
    return False


class ClaimGuard:
    """Streams the reply a sentence at a time and drops sentences that claim a change that didn't happen.

    ponytail: pattern-based, so an oddly worded false claim can still slip through; the receipt stays the record.
    """

    def __init__(self, facts: dict):
        self.facts, self.buffer, self.dropped = facts, "", 0

    def _keep(self, sentence: str) -> bool:
        if sentence.strip() and _false_claim(sentence, self.facts):
            self.dropped += 1
            logger.info("Dropped a reply sentence claiming a change that didn't happen: %r", sentence.strip())
            return False
        return True

    def feed(self, token: str) -> str:
        self.buffer += token
        out, start = "", 0
        for match in _SENTENCE_END.finditer(self.buffer):
            piece = self.buffer[start:match.end()]
            out += piece if self._keep(piece[:match.start() - start]) else ""
            start = match.end()
        self.buffer = self.buffer[start:]
        return out

    def flush(self) -> str:
        rest, self.buffer = self.buffer, ""
        return rest if self._keep(rest) else ""


def guard_reply(text: str, facts: dict) -> str:
    guard = ClaimGuard(facts)
    return (guard.feed(text) + guard.flush()).strip()


def hold_reply(copilot: dict, text: str) -> str:
    """Keep the reply on its day so switching back replays it without a model call."""
    engine.day_of(copilot, copilot["current_day"])["reply"] = text
    return text


async def respond(state: CopilotState) -> dict:
    facts, copilot = state["facts"], state["copilot"]
    day = engine.day_of(copilot, copilot["current_day"])
    if state.get("nav_only") and day.get("reply"):
        return {"assistant_response": day["reply"]}  # coming back to a day replays what was said there
    if state.get("defer_reply"):
        return {}  # the caller streams the reply itself (see reply_prompt / hold_reply)
    try:
        from app.services.admin_settings import active_instruction
        text = await llm_service.generate(
            prompt=reply_prompt(facts), system_instruction=active_instruction("copilot_reply", RESPOND_INSTRUCTION), temperature=0.6,
        )
    except Exception as exc:
        logger.warning("Copilot respond failed: %s", exc)
        text = ""
    text = hold_reply(copilot, guard_reply(str(text or ""), facts) or _fallback_reply(facts))
    return {"copilot": copilot, "assistant_response": text}


def render(state: CopilotState) -> dict:
    copilot = state["copilot"]
    return {"itinerary_graph": build_itinerary_graph(
        markdown=engine.to_markdown(copilot), origin=state.get("origin"),
        destination=copilot["destination"], duration_days=len(copilot["days"]),
        extracted=engine.to_extracted(copilot, state.get("origin")),
    )}


def build_copilot_graph():
    graph = StateGraph(CopilotState)
    graph.add_node("init", init)
    graph.add_node("interpret", interpret)
    graph.add_node("apply", apply)
    graph.add_node("research", research_node)
    graph.add_node("recommend", recommend)
    graph.add_node("respond", respond)
    graph.add_node("render", render)
    graph.add_conditional_edges(START, lambda s: "interpret" if s.get("copilot") else "init",
                                {"init": "init", "interpret": "interpret"})
    graph.add_edge("init", "research")
    graph.add_edge("interpret", "apply")
    graph.add_conditional_edges("apply", lambda s: "research" if s.get("queries") else "recommend",
                                {"research": "research", "recommend": "recommend"})
    graph.add_edge("research", "recommend")
    graph.add_edge("recommend", "respond")
    graph.add_edge("respond", "render")
    graph.add_edge("render", END)
    return graph.compile()


copilot_graph = build_copilot_graph()


def _turn_input(state: dict[str, Any], defer_reply: bool = False) -> dict:
    copilot = copy.deepcopy(state.get("copilot")) if state.get("copilot") else None
    focus = state.get("focus_day")
    if copilot and type(focus) is int and 1 <= focus <= len(copilot["days"]):
        copilot["current_day"] = focus  # the day the traveler is looking at in the client
    return {"copilot": copilot, "defer_reply": defer_reply,
            **{k: state.get(k) for k in ("user_message", "ui_action", "destination", "duration_days", "origin",
                                         "currency", "budget_target", "planning_preferences",
                                         "places_to_visit", "user_name", "recent")}}


_ASKS_FOR_CHANGE = re.compile(r"\b(add|swap|move|replace|remove|delete|drop|switch|shift|put)\b", re.I)
REASON_WORDS = {"hard_avoid": "it clashes with your no-{} rule", "rejected_before": "you turned it down before"}


def _reason_text(reason: dict) -> str:
    if reason.get("code") == "unreachable":
        hours = reason.get("one_way_hours") or reason.get("travel_hours") or 0
        return f"too far from {reason.get('from') or 'the base'} for this day (about {hours:g} h travel)"
    template = REASON_WORDS.get(reason.get("code"))
    return template.format(", ".join(reason.get("detail") or [])) if template else reason.get("code", "could not fit")


def turn_changes(facts: dict | None) -> list[dict]:
    """The receipt shown under the reply: the engine's own record of this turn, never the model's."""
    facts = facts or {}
    skipped = [{"status": "skipped", "text": f"Didn't add {b['name']} to day {b['day']}: " + "; ".join(
        _reason_text(r) for r in b["reasons"]),
        **({} if any(r["code"] == "unreachable" for r in b["reasons"]) else
           {"retry": {"op": "add", "name": b["name"], "day": b["day"], "force": True}})}
        for b in facts.get("blocked") or []]
    changes = [{"status": "done", "text": event} for event in facts.get("events") or []] + skipped
    # A request to change the plan that changed nothing (the interpreter found no operation in it) says so,
    # rather than leaving the reply's own account as the only record.
    message = (facts.get("user_message") or "").strip()
    if not changes and _ASKS_FOR_CHANGE.search(message) and not message.endswith("?"):
        changes = [{"status": "skipped", "text": "Nothing in the plan changed"}]
    return changes


def _turn_output(result: dict) -> dict:
    return {"copilot": result["copilot"], "assistant_response": result.get("assistant_response") or "",
            "itinerary_graph": result["itinerary_graph"], "facts": result.get("facts"), "ui_action": None,
            "changes": turn_changes(result.get("facts"))}


async def build_with_agent(state: dict[str, Any]) -> dict:
    """Main-graph adapter: run one build-with-agent turn and hand back the updated copilot."""
    return _turn_output(await copilot_graph.ainvoke(_turn_input(state)))


STAGES = {"init": "Reading traveler tips from Reddit and Quora", "interpret": "Working that into your plan",
          "research": "Checking what travelers recommend", "recommend": "Updating your day"}


async def stream_build_with_agent(state: dict[str, Any]):
    """Run a turn node by node for the streaming API.

    Yields ("stage", label) while it works, then ("result", output). The reply is left
    for the caller to stream from output["facts"]; a pure day switch returns the held reply.
    """
    merged = _turn_input(state, defer_reply=True)
    last_label = None
    async for update in copilot_graph.astream(merged, stream_mode="updates"):
        for node, delta in update.items():
            merged.update(delta or {})
            label = STAGES["research"] if node == "apply" and (delta or {}).get("queries") else STAGES.get(node)
            if label and label != last_label:
                last_label = label
                yield "stage", label
    yield "result", _turn_output(merged)
