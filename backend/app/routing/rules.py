"""Deterministic half of the System 1 router.

Confident rules decide an unambiguous message with no model call. Anything hedged,
mixed, or flagged still goes to Jev and then the writer. The patterns follow the
original router: a question is an ask, a change to the current plan is a modify,
and "add a temple on day 2" is that day, not a new set of dates.
"""

from __future__ import annotations

import re
from typing import Any

from app.routing.models import Intent, RouteDecision, TripSnapshot

MODIFY_VERBS = r"(change|swap|replace|switch|move|add|remove|drop|cancel|upgrade|downgrade|shorten|extend|make it|turn it|adjust|update)"
PLAN_VERBS = r"(plan|planning|itinerary|organi[sz]e|arrange|build|create|design|route|schedule)"
ASK_STARTERS = r"^(what|where|when|which|who|why|how|is|are|can|could|do|does|should|will|would|any)\b"
_HEDGES = (
    r"\b(not sure|maybe|perhaps|i don'?t know|dunno|whatever|surprise me|never ?mind|"
    r"something|somewhere|anything|thinking (of|about)|what if|what about|rather)\b"
)
_COMMAND_QUESTION = r"^(please\b|(can|could|would|will) (you|we)\b)"
_WORD_MODIFY = rf"\b{MODIFY_VERBS}\b"
_TRAVEL_ANCHORS = (
    r"\b(hotels?|hostels?|ryokan|rooms?|stay|check[- ]?(in|out)|flights?|fly|trains?|stations?|airport|"
    r"tickets?|fares?|tram|ferry|bus|metro|mtr|subway|taxi|weather|rain\w*|forecast|typhoon|"
    r"temperature|umbrellas?|budget|cost|price|pay|total|day\s*\d+|(first|second|third|last) "
    r"(day|morning|afternoon|evening|night)|walk\w*|open|opening|close[sd]?|hours|museums?|"
    r"temples?|shrines?|castle|park|market|restaurants?|cafe|food|meals?|lunch|dinner|breakfast|"
    r"vegetarian|vegan|halal|stops?|trip|visit\w*|morning|afternoon|evening)\b"
)
_TRIP_DETAIL = (
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|a)\s+(days?|nights?|weeks?)\b|"
    r"\b(week|weekend|fortnight)\b"
)
_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous |prior |above )?instructions|reveal (your |the )?(system |hidden )?prompt|"
    r"you are now|disregard (the |your )?rules",
    re.I,
)
_AGREE = re.compile(
    r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|please|do it|do that|go ahead|go for it|sounds good|"
    r"let'?s do (it|that)|perfect|great|confirm(ed)?|apply|finali[sz]e|lock it in|save it|"
    r"that'?s (final|it)|update (the )?(studio|plan|itinerary))\b",
    re.I,
)
_SOCIAL = re.compile(
    r"^\W*(thanks|thank you|thx|hi|hello|hey|good (morning|afternoon|evening)|cool|nice|awesome|lol|haha)\b",
    re.I,
)
RULES_FIRST = "rules-first: one unambiguous signal"

_NUMBER = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
_SPAN = rf"(?:{_NUMBER}|an?|a couple of|a few|several)\s+(?:more\s+)?(?:days?|nights?|weeks?|months?)"
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_ORDINAL = r"\d{1,2}(?:st|nd|rd|th)?"
_WHEN = rf"(?:{_ORDINAL}(?:\s*(?:-|–|to|until|and)\s*{_ORDINAL})?\s+)?{_MONTH}\b|next (?:week|month|year)\b"
_SHIFT = r"(?:move|shift|push|slide)"
_TRIP = r"(?:trip|holiday|vacation|stay|itinerary|plan|everything|whole)"
_LENGTH = rf"(?:an?\s+)?{_NUMBER}[\s-]+(?:days?|nights?)"
_NOT_A_TRIP_TO = r"(?!\s+(?:trip|stay|holiday|vacation|getaway|break|visit|itinerary)\s+(?:to|in|at|for)\b)"
_NEW_DATES = [re.compile(p) for p in (
    r"\b(?:change|update|set|use|apply|alter|adjust|edit|pick|choose|new|different|other)\s+(?:\w+\s+){0,2}?dates?\b",
    r"\b(?:move|shift|push)\b\s+(?:\w+\s+){0,2}?dates?\b",
    rf"\b{_SHIFT}\b\s+(?:\w+\s+){{0,2}}?{_TRIP}\b",
    rf"\b(?:bring|put)\s+(?:\w+\s+){{0,2}}?{_TRIP}\s+(?:forward|back|off|later|earlier)\b",
    r"\b(?:postpon\w*|reschedul\w*)\b",
    rf"\b(?:extend|shorten|lengthen)\w*\b.{{0,24}}?\b(?:{_TRIP}|dates?|it|{_SPAN})\b",
    rf"\b(?:make|turn|change|cut|stretch)\s+(?:it|the trip|the stay|this)\s+(?:(?:in)?to\s+|(?:down|up) to\s+)?"
    rf"(?:an?\s+)?{_NUMBER}[\s-]+(?:days?|nights?)\b",
    r"\b(?:number|length) of (?:the )?(?:days|nights|trip|stay)\b",
    rf"\b{_SHIFT}\b.{{0,24}}?\b(?:to|in|on|until|for)\s+(?:{_WHEN})",
    rf"\b(?:add|remove|drop|cut)\s+(?:an?|one|{_NUMBER})\s+(?:extra\s+|more\s+)?(?:days?|nights?)\b",
    rf"\b(?:{_NUMBER}|an?|a few|several|a couple of)\s+(?:more|extra|additional)\s+(?:days?|nights?)\b",
)]
_LENGTH_SAID = [re.compile(p) for p in (
    rf"\bonly\s+(?:(?:have|need|want|got|do)\s+)?{_LENGTH}\b",
    rf"\b(?:let'?s|we|i)(?:'d|'ll| would| will)?\s+(?:only\s+)?(?:do|stay|want|need|like|take|have|go for)\s+"
    rf"{_LENGTH}\b{_NOT_A_TRIP_TO}",
    rf"\b{_NUMBER}[\s-]+(?:days?|nights?)\s+(?:instead|only|please|in total|total)\b",
)]
_SPAN_DATES = [re.compile(p) for p in (
    rf"\b{_SPAN}\s+(?:later|earlier|sooner|longer|shorter|back|forward)\b",
    rf"\b(?:back|forward|ahead)\s+(?:by\s+)?{_SPAN}\b",
)]
_MAKE_THE_TRIP = re.compile(
    r"\bmake\s+(?:this|that|the|my|our)\s+(?:trip|itinerary|plan|stay|holiday|vacation)\s+(?:a\s+(?:bit|little)\s+)?"
    r"(?:cheaper|longer|shorter)\b"
)
GRANDER_WORDS = r"\b(?:gr+and(?:er|est)|(?:more|so)\s+gr+and|(?:more\s+)?luxurious|luxury|upscale|fancier|posher)\b"
_CONCESSIVE_BUDGET = re.compile(
    r"\beven\s+(?:if|it|though|when)\b[^.,;!?]{0,60}?"
    r"\b(?:over|out(?:\s+of)?|above|beyond|exceeds?|blows?)\s+(?:the\s+|my\s+|our\s+)?(?:budget|cost|price)\b"
    r"|\bno\s+matter\s+(?:what\s+)?(?:the\s+|it\s+)?(?:budget|cost|costs|price)\b"
    r"|\bwhatever\s+(?:it\s+costs?|the\s+(?:cost|price|budget))\b"
    r"|\bregardless\s+of\s+(?:the\s+)?(?:budget|cost|price)\b"
)


def message_flagged(message: str) -> bool:
    """A message that tries to override the route. It never takes the rules-only shortcut."""
    return bool(_INJECTION.search(message or ""))


def asks_for_new_dates(message: str) -> bool:
    lower = (message or "").lower()
    return any(p.search(lower) for p in (*_NEW_DATES, *_LENGTH_SAID, *_SPAN_DATES))


def _says_a_length_only(lower: str) -> bool:
    return (
        any(p.search(lower) for p in _LENGTH_SAID)
        and not any(p.search(lower) for p in _NEW_DATES)
        and not any(p.search(lower) for p in _SPAN_DATES)
    )


def _changes_the_dates(lower: str, has_plan: bool) -> bool:
    if not has_plan:
        return asks_for_new_dates(lower) and not _says_a_length_only(lower)
    return asks_for_new_dates(lower)


def without_concessive_budget(lower: str) -> str:
    return _CONCESSIVE_BUDGET.sub(" ", lower)


def affected_parts_for(message: str) -> list[str]:
    """Which slice of the plan a change is about. A numbered day is that day, not a new trip length."""
    lower = (message or "").lower()
    parts: list[str] = []
    mapping = [
        ("hotel", r"\b(hotel|hostel|stay|accommodation|room|ryokan)\b|" + GRANDER_WORDS),
        ("ticket", r"\b(flight|fly|train|ticket|fare|seat|rail|shinkansen)\b"),
        ("attraction", r"\b(attraction|sight|museum|temple|shrine|park|visit|stop|activity|eat|eating|food|meal|dinner|lunch|breakfast|restaurant|cafe)\b"),
        ("weather", r"\b(weather|rain|forecast|typhoon|temperature)\b"),
        ("budget", r"\b(budget|cost|cheaper|expensive|money|price)\b|\bspend\s+(?:less|more|under|over)\b"),
    ]
    for name, pattern in mapping:
        text = without_concessive_budget(lower) if name == "budget" else lower
        if re.search(pattern, text):
            parts.append(name)
    if asks_for_new_dates(lower):
        parts.append("dates")
    for first, second in re.findall(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", lower):
        value = first or second
        if value:
            parts.append(f"day_{int(value)}")
    for word, index in {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}.items():
        if re.search(rf"\b{word}\s+day\b", lower):
            parts.append(f"day_{index}")
    return sorted(set(parts))


def looks_like_new_trip(message: str) -> bool:
    lower = (message or "").lower()
    return bool(
        re.search(r"\b(new|another|different)\s+(trip|itinerary|plan)\b", lower)
        or re.search(r"\b(plan|create|build)\b.{0,24}\b(trip|itinerary)\b", lower)
        or re.search(r"\bmake\b(?!\s+(?:it|that|this|the|my|our)\b).{0,20}\b(trip|itinerary)\b", lower)
        or re.search(r"\b(start over|from scratch|re-?plan|plan again)\b", lower)
    )


def wants_to_clear_plan(message: str) -> bool:
    """Drop the saved itinerary. Removing one stop or hotel stays a normal edit."""
    lower = (message or "").lower()
    direct = r"\b(delete|clear|wipe|erase|scrap|discard|drop|cancel)\s+(?:the\s+|my\s+|our\s+)?(?:whole\s+|entire\s+|full\s+)?(?:plan|itinerary)\b"
    remove_whole = r"\bremove\s+(?:the\s+|my\s+|our\s+)?(?:whole\s+|entire\s+|full\s+)(?:plan|itinerary)\b"
    named_then_verb = r"\b(whole|entire|full)\s+(?:plan|itinerary)\b.{0,40}\b(delete|remove|clear|wipe|erase|scrap|discard|drop)\b"
    wipe_all = r"\b(delete|remove|clear|wipe|erase|scrap|discard|drop)\b.{0,40}\b(whole thing|everything|all of it)\b"
    return bool(re.search(f"{direct}|{remove_whole}|{named_then_verb}|{wipe_all}", lower))


def _names_a_place(text: str) -> bool:
    return any(word[:1].isupper() and word not in {"I", "I'd", "I'm"} for word in text.split()[1:])


def _known_names(snapshot: TripSnapshot, plan_summary: dict[str, Any] | None) -> list[str]:
    names = [snapshot.destination or ""]
    if plan_summary:
        names.append(str(plan_summary.get("destination") or ""))
        for day in plan_summary.get("days") or []:
            if isinstance(day, dict):
                names.extend(str(item.get("name") or "") for item in day.get("items") or [] if isinstance(item, dict))
    return [name for name in names if len(name) >= 3]


def rules_decide_alone(
    message: str,
    decision: RouteDecision,
    *,
    snapshot: TripSnapshot,
    plan_summary: dict[str, Any] | None,
) -> bool:
    if decision.reason_code in {"accept_offer", "no_pending_change", "social", "budget_saved"}:
        return True
    text = (message or "").strip()
    lower = text.lower()
    words = re.findall(r"[a-z0-9']+", lower)
    if len(words) < 3 or not text.isascii() or re.search(_HEDGES, lower):
        return False
    is_question = bool(re.match(ASK_STARTERS, lower)) or text.endswith("?")
    command_question = bool(re.match(_COMMAND_QUESTION, lower))
    plan_hit = bool(re.search(PLAN_VERBS, lower)) or bool(re.search(r"\b\d+\s*-?\s*(day|night)s?\b", lower))
    modify_hit = (
        bool(re.search(_WORD_MODIFY, lower))
        or _changes_the_dates(lower, plan_summary is not None)
        or _MAKE_THE_TRIP.search(lower) is not None
    )
    if decision.intent == Intent.ASK:
        known = any(name.lower() in lower for name in _known_names(snapshot, plan_summary))
        return is_question and not plan_hit and not modify_hit and (bool(re.search(_TRAVEL_ANCHORS, lower)) or known)
    if decision.intent == Intent.MODIFY:
        return (
            plan_summary is not None
            and modify_hit
            and (not is_question or command_question)
            and not re.search(r"\b(new|another|different|fresh)\s+(trip|itinerary|plan)\b", lower)
        )
    if decision.intent == Intent.PLAN:
        if is_question and not command_question:
            return False
        travel_hit = bool(re.search(r"\b(trip|travel|holiday|vacation|visit|go to|going to)\b", lower))
        detail = bool(re.search(_TRIP_DETAIL, lower)) or _names_a_place(text)
        ready = plan_summary is None and not snapshot.missing_slots() and bool(snapshot.destination)
        return (plan_hit or travel_hit) and (detail or ready)
    return False


class RuleRouter:
    def route(
        self,
        *,
        message: str,
        snapshot: TripSnapshot,
        plan_summary: dict[str, Any] | None,
        last_reply: str | None = None,
    ) -> RouteDecision:
        text = (message or "").strip()
        lower = text.lower()
        has_plan = plan_summary is not None or snapshot.has_plan
        missing = snapshot.missing_slots()
        plan = plan_summary if has_plan else None

        if has_plan and _AGREE.match(text) and not re.search(_WORD_MODIFY, lower):
            if last_reply and "?" in last_reply:
                return RouteDecision(
                    intent=Intent.MODIFY, confidence=0.92, need_clarification=False,
                    reason_code="accept_offer", affected_parts=affected_parts_for(last_reply),
                    missing_slots=missing, related_to_plan=True, rationale=RULES_FIRST,
                )
            return RouteDecision(
                intent=Intent.ASK, confidence=0.9, need_clarification=False,
                reason_code="no_pending_change", missing_slots=missing, related_to_plan=True,
                rationale=RULES_FIRST,
            )
        if has_plan and _SOCIAL.match(text) and not re.search(_WORD_MODIFY, lower):
            return RouteDecision(
                intent=Intent.ASK, confidence=0.9, need_clarification=False, reason_code="social",
                missing_slots=missing, related_to_plan=True, rationale=RULES_FIRST,
            )
        # A budget figure with no change verb is saved by the ledger. It is not a rewrite.
        if has_plan and re.search(r"\bbudget\b", lower) and not re.search(_WORD_MODIFY, lower) and not (
            bool(re.match(ASK_STARTERS, lower)) or text.endswith("?")
        ):
            return RouteDecision(
                intent=Intent.ASK, confidence=0.9, need_clarification=False, reason_code="budget_saved",
                affected_parts=["budget"], missing_slots=missing, related_to_plan=True, rationale=RULES_FIRST,
            )

        affected = affected_parts_for(text)
        is_question = bool(re.match(ASK_STARTERS, lower)) or text.endswith("?")
        modify_hit = (
            bool(re.search(MODIFY_VERBS, lower))
            or _changes_the_dates(lower, has_plan)
            or _MAKE_THE_TRIP.search(lower) is not None
        )
        plan_hit = bool(re.search(PLAN_VERBS, lower)) or bool(re.search(r"\b\d+\s*(day|night)s?\b", lower))
        travel_hit = bool(re.search(r"\b(trip|travel|holiday|vacation|visit|go to|going to)\b", lower))

        if is_question and not modify_hit:
            intent, confidence, reason = Intent.ASK, 0.9 if has_plan else 0.82, "question"
        elif modify_hit and has_plan and not looks_like_new_trip(lower):
            intent, confidence, reason = Intent.MODIFY, 0.86, "change_current_plan"
        elif plan_hit or travel_hit:
            intent, confidence, reason = Intent.PLAN, 0.84 if (snapshot.destination or missing) else 0.6, "new_trip_request"
        else:
            intent, confidence, reason = Intent.UNCLEAR, 0.45, "no_travel_signal"

        if intent == Intent.MODIFY and not has_plan:
            intent, confidence, reason = Intent.UNCLEAR, 0.7, "no_plan_to_modify"
        need = bool(missing) or intent == Intent.UNCLEAR
        if intent == Intent.PLAN and missing:
            need, reason = True, "missing_" + "_".join(missing[:2])
        return RouteDecision(
            intent=intent, confidence=confidence, need_clarification=need, reason_code=reason,
            affected_parts=affected if intent == Intent.MODIFY else [],
            missing_slots=missing, related_to_plan=has_plan, rationale="rule-based fallback",
        )
