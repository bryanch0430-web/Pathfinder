"""Quick-question path: answer from the plan first, at most one allowlisted tool."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from app.application.llm_gateway import LLMGateway
from app.application.prompt_context import compact_json, conversation_block, earlier_turns, plan_outline, with_conversation
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import CanaryLeak, ProviderError
from app.domain.models import FailureKind, TripContext, TripPlan
from app.infrastructure.http import classify_exception
from app.security import fence_user_message, strip_fence_tags

WEATHER_WORDS = r"\b(weather|rain|forecast|temperature|hot|cold|typhoon)\b"
DISTANCE_WORDS = (
    r"\b(distance|how far|travel time|how long|get to|walk|closest|nearest|"
    r"near(?:est|by)?|farthest|furthest)\b"
)
HOURS_WORDS = r"\b(open|opening|hours|close|closed)\b"
#: opinion questions about one place: "what is good about Amber", "is it worth it"
QUALITY_WORDS = (
    r"\b(good|great|worth|best|recommend|recommended|review|reviews|rating|rated|"
    r"famous|known for|why|should i|any good|is it|popular|signature|speciality|specialty)\b"
)
#: a follow-up that only makes sense against the previous answer
FOLLOW_UP_WORDS = (
    r"^\s*(really|why|how come|and|so|hmm|sure|ok|okay|right|go on|continue|"
    r"more|elaborate|explain|what do you mean|how so|is that true|are you sure)"
    r"(\b|$)"
)

#: questions that want more than a lookup — these are allowed to use the slower,
#: synthesis-capable search when it is switched on
DEEP_SEARCH_WORDS = (
    r"\b(research|in depth|compare|comparison|overview|guide|worth visiting|"
    r"best time|best area|nightlife|food scene|safety|safe|vibe|atmosphere|"
    r"local tips|hidden gems?)\b"
)


#: What a weather question is told when no place or forecast was found: nothing failed.
_NO_FORECAST = "No forecast could be found for that place and those dates."


def _forecast_unavailable(failure: ProviderError) -> str:
    """Why there is no forecast to give: nothing was found, or a lookup failed (and whether asking again could help)."""
    if failure.kind is FailureKind.NO_DATA:
        return _NO_FORECAST
    if failure.kind is FailureKind.BLOCKED:
        return "This turn reached its limit on data lookups, so there is no forecast to give."
    text = f"The weather lookup failed ({failure.kind.value}), so there is no forecast to give."
    return f"{text} Asking again later may work." if failure.retryable else text


class AskPath:
    def __init__(self, *, gateway: LLMGateway, registry: Any, settings: Settings):
        self.gateway = gateway
        self.registry = registry
        self.settings = settings

    async def answer(
        self,
        *,
        question: str,
        plan: TripPlan | None,
        context: TripContext,
        toolbox: Toolbox,
        emit,
        history: list[Any] | None = None,
        scope_item_id: str | None = None,
    ) -> tuple[str, bool, str]:
        if plan and re.search(
            r"\b(what did you (?:change|update|add|remove)|what (?:has |have )?changed|"
            r"what was (?:changed|added|removed)|last change|show (?:me )?(?:the )?(?:change|diff)|"
            r"(?:what|which)\b.{0,30}\b(?:have|did) you)\b",
            question.lower(),
        ):
            updates = [
                message
                for message in (history or [])
                if getattr(message, "role", "") == "assistant"
                and "Updated to version" in str(getattr(message, "content", ""))
            ]
            if updates:
                return str(updates[-1].content), True, "plan"
            return "No itinerary changes have been made yet. This is still the first version.", True, "plan"

        # Every answer the writer phrases reads the recent conversation, with or without a plan: a follow-up
        # ("and the weather there?", "really?") names its subject only in an earlier turn.
        facts = _plan_facts(question, plan, history, scope_item_id) if plan else {}
        # A question about changes already carries the earlier turns as `recent_changes`, with their diffs.
        conversation = "" if "recent_changes" in facts else conversation_block(history or [], question)
        related = _relates_to_plan(question, plan, facts, scope_item_id)
        if not related:
            # The itinerary rows a bare "what" attached are not an answer to a
            # question about something else; the writer still sees the outline.
            facts.pop("itinerary", None)
        lower = question.lower()
        needs_live_tool = (
            (re.search(WEATHER_WORDS, lower) and "weather" not in facts)
            or (
                re.search(DISTANCE_WORDS, lower)
                and "distances" not in facts
                and "route" not in facts
            )
            or (re.search(HOURS_WORDS, lower) and "item" not in facts)
            or (re.search(QUALITY_WORDS, lower) and "place_detail" not in facts)
            or (
                re.search(DEEP_SEARCH_WORDS, lower)
                and "web" not in facts
                and getattr(self.registry, "web", None)
            )
            or (
                re.search(r"\b(japanese|sushi|ramen|chinese|korean|thai|italian|cuisine|serves)\b", lower)
                and "web" not in facts
                and getattr(self.registry, "web", None)
            )
        )
        # A numeric distance for the selected stop is answered from the numbers
        # themselves; a writer that opens with "the plan does not contain the
        # hotel" would bury the one figure the traveller asked for.
        focus = (facts.get("distances") or {}).get("focus")
        if focus:
            block = facts["distances"]
            return (
                f"{focus['name']} is about {focus['km']} km from {block['reference']} "
                f"({block.get('method', 'straight-line estimate')})."
            ), related, "plan"
        # A question about a section the plan does not have is answered by the
        # recorded reason, not by the writer guessing around it. A distance
        # question still gets its numbers, with the missing hotel named.
        if facts.get("missing") and "distances" not in facts:
            return _fallback_answer(facts, "plan"), related, "plan"
        if facts and not needs_live_tool:
            text = await self._phrase(question, plan, facts, "plan", conversation)
            return text, related, "plan"

        tool_name, tool_result, source = await self._one_tool(
            question, plan, context, toolbox, scope_item_id=scope_item_id
        )
        if tool_name == "web" and _cuisine_word(question):
            subject = ""
            if plan is not None:
                picked = _scoped_item(plan, scope_item_id) or _find_item_strict(plan, question)
                subject = picked.name if picked is not None else ""
            return _cuisine_from_pages(question, tool_result or [], subject), True, source or "web"
        if tool_result is not None:
            # Keep the fact key meaningful: the writer has instructions for
            # web results and place detail, but not for an anonymous "tool" blob.
            named = {"web", "web_answer", "place_detail"}
            key = tool_name if tool_name in named else "tool"
            text = await self._phrase(question, plan, {key: tool_result}, source or tool_name, conversation)
            return text, related, source or tool_name
        if facts:
            text = await self._phrase(question, plan, facts, "plan", conversation)
            return text, related, "plan"
        if plan is not None and not related:
            # Not about this trip and no tool applies: one writer call with no
            # facts, which says the plan does not hold it rather than guessing.
            text = await self._phrase(question, plan, {}, "none", conversation)
            return text, False, "none"
        if plan is not None:
            # Nothing matched a specific trigger, but the plan itself is context:
            # let the writer answer from the itinerary and weather rather than
            # refuse a question it has the material for.
            fallback = dict(facts)
            fallback.setdefault("itinerary", _itinerary_rows(plan))
            weather_rows = [
                day.weather.model_dump(mode="json") for day in plan.days if day.weather is not None
            ]
            if weather_rows:
                fallback.setdefault("weather", weather_rows)
            text = await self._phrase(question, plan, fallback, "plan", conversation)
            return text, True, "plan"
        text = (
            "There is no plan yet, so I have nothing to answer from. "
            "Tell me where and when you are going and I will build one first."
        )
        return text, related, "none"

    async def _one_tool(
        self,
        question: str,
        plan: TripPlan | None,
        context: TripContext,
        toolbox: Toolbox,
        scope_item_id: str | None = None,
    ) -> tuple[str, Any, str]:
        lower = question.lower()
        if re.search(WEATHER_WORDS, lower) and self.registry.weather:
            target_date = _mentioned_date(question, plan) or _named_date(question, plan)
            start = target_date or (plan.dates.start if plan else context.start_date)
            end = target_date or (plan.dates.end if plan else context.end_date)
            if start and end:
                source = self.registry.weather[0]
                try:
                    location = _destination_geo(plan, context)
                    if location is None and context.destination:
                        location = await toolbox.call(
                            "geocode",
                            self.registry.geocoder.geocode,
                            name=context.destination,
                        )
                    if location is None:
                        return "weather", {"unavailable": _NO_FORECAST}, "weather"
                    result = await toolbox.call(
                        "weather.forecast",
                        source.forecast,
                        geo=location,
                        start=start,
                        end=end,
                    )
                    return "weather", [row.model_dump(mode="json") for row in result], source.name
                except Exception as exc:
                    return "weather", {"unavailable": _forecast_unavailable(classify_exception(exc, "weather"))}, "weather"
        scoped_dist = _scoped_item(plan, scope_item_id)
        if re.search(DISTANCE_WORDS, lower) and self.registry.routes and scoped_dist is None:
            points = _two_points(plan, question)
            if points:
                source = self.registry.routes[0]
                try:
                    leg = await toolbox.call("routes.leg", source.leg, origin=points[0], destination=points[1], mode="walk")
                    return "route", leg.model_dump(mode="json"), source.name
                except Exception:
                    return "route", None, "route"
        if re.search(HOURS_WORDS, lower):
            item = _find_item(plan, question)
            if item:
                return "plan_item", {
                    "name": item.name,
                    "notes": item.notes,
                    # The source's stated hours for the visit day, never filled in.
                    "opening_hours": item.opening_hours if item.hours_status == "stated" else "unknown",
                    "sources": [ref.model_dump(mode="json") for ref in item.source_refs],
                }, "plan"
        # "What is good about Amber?" — the plan holds only a one-line summary.
        # Ask the place source for the rating, review count and editorial summary
        # it already returns; that is structured and citable, unlike a web result.
        if plan is not None and self.registry.places and re.search(QUALITY_WORDS, lower):
            item = _scoped_item(plan, scope_item_id) or _find_item_strict(plan, question)
            if item is not None:
                source = self.registry.places[0]
                category = "restaurant" if item.type.value == "meal" else "attraction"
                try:
                    rows = await toolbox.call(
                        "places.search",
                        source.search,
                        destination=plan.destination.name,
                        geo=plan.destination.geo,
                        category=category,
                        limit=5,
                        query=f"{item.name} {plan.destination.name}",
                    )
                    detail = _place_detail(rows, item.name)
                    if detail is not None and (detail.get("rating") is not None or detail.get("summary")):
                        return "place_detail", detail, source.name
                except Exception:
                    pass
                # The place source had nothing useful: fall through to web search.

        # Open questions the plan cannot answer. DeepSeek Search is sub-second and
        # returns structured, citable results, so it is the default. Grok's Agent
        # Tools search synthesises across sources but takes ~20 s, so it is only
        # used for synthesis questions and only when explicitly enabled.
        if plan is not None and getattr(self.registry, "web", None):
            item = _scoped_item(plan, scope_item_id) or _find_item_strict(plan, question)
            subject = item.name if item is not None else ""
            if subject or re.search(QUALITY_WORDS, lower) or re.search(DEEP_SEARCH_WORDS, lower):
                destination = plan.destination.name
                if subject:
                    query = f"{subject} {destination} {_keyword_query(question, '')}".strip()
                else:
                    query = _keyword_query(question, destination)
                deep = bool(
                    self.settings.grok_search_enabled
                    and getattr(self.registry, "web_deep", None)
                    and re.search(DEEP_SEARCH_WORDS, lower)
                )
                try:
                    if deep:
                        answer = await toolbox.call(
                            "web.search", self.registry.web_deep[0].answer, query=query
                        )
                        return "web_answer", answer.as_dict(), answer.source_name
                    rows = await toolbox.call(
                        "web.search",
                        self.registry.web[0].search,
                        query=query[:128],
                        count=4,
                        ori_question=question[:128],
                    )
                    rows = [row.as_dict() for row in rows][:4]
                    if _cuisine_word(question):
                        return "web", rows, self.registry.web[0].name
                    return "web", rows, self.registry.web[0].name
                except Exception:
                    return "web", [], "web"
        return "none", None, "none"
    async def _phrase(
        self,
        question: str,
        plan: TripPlan | None,
        facts: dict,
        source: str,
        conversation: str = "",
    ) -> str:
        if self.gateway.available:
            user = ask_prompt(question, plan, facts, source, conversation)
            try:
                data = await self.gateway.json("ask", user=user, purpose="ask.answer")
                answer = str(data.get("answer") or "").strip()
                if answer:
                    return answer
            except CanaryLeak:
                raise
            except Exception:
                pass
        return _fallback_answer(facts, source)


def ask_prompt(question: str, plan: TripPlan | None, facts: dict, source: str, conversation: str = "") -> str:
    """The writer's input for one answer (Proposal TC-10: only what the answer reads).

    The question is sent once, fenced. The plan is a day-by-day outline, or just
    its header line when the facts already carry the itinerary rows. Facts are
    compact JSON without empty values. ``conversation`` is the recent conversation
    block (`conversation_block`), which never holds the question itself.
    """
    if plan is None:
        outline = "none"
    else:
        outline = plan_outline(plan.summary_for_prompt())
        if "itinerary" in facts:
            outline = outline.splitlines()[0]
    return (
        f"Plan: {outline}\n"
        f"Facts (source: {source}): {compact_json(facts)[:12000]}\n\n"
        + with_conversation(conversation)
        + fence_user_message(question)
    )


def _scoped_item(plan: TripPlan | None, scope_item_id: str | None):
    """The stop the traveller selected before typing, if it is still in the plan."""
    if not plan or not scope_item_id:
        return None
    found = plan.find_item(scope_item_id)
    return found[1] if found else None


def _find_item_strict(plan: TripPlan | None, question: str):
    """Like _find_item, but never matches on the destination name alone.

    "what is the nightlife like in Hong Kong?" shares "hong"/"kong" with every
    stop, which made the loose matcher hand "Hong Kong Disneyland" to a
    nightlife question and then search the wrong thing.
    """
    if not plan:
        return None
    lower = question.lower()
    for day in plan.days:
        for item in day.items:
            if item.name.lower() in lower:
                return item
    destination_words = {w for w in re.findall(r"[a-z]{4,}", plan.destination.name.lower())}
    words = {w for w in re.findall(r"[a-z]{4,}", lower)} - destination_words
    if not words:
        return None
    best = None
    best_score = 0
    for day in plan.days:
        for item in day.items:
            item_words = {w for w in re.findall(r"[a-z]{4,}", item.name.lower())} - destination_words
            score = len(words & item_words)
            if score > best_score:
                best, best_score = item, score
    return best if best_score >= 1 else None


# Scheduling details on a plan item. An hours question gets the stated hours from
# its own fact (see the HOURS_WORDS lookup); other item facts keep their old size.
_ITEM_SCHEDULE_FIELDS = {"opening_hours", "hours_status", "transfer"}


def _plan_facts(
    question: str,
    plan: TripPlan,
    history: list[Any] | None = None,
    scope_item_id: str | None = None,
) -> dict:
    lower = question.lower()
    facts: dict[str, Any] = {}
    if re.search(FOLLOW_UP_WORDS, lower) or len(lower.strip(" ?!")) <= 8:
        # "really?" / "why?" has no subject of its own: the answer continues the
        # previous exchange, which the prompt's recent conversation block holds,
        # instead of describing the itinerary. The flag says so; the turns are
        # not repeated here.
        if earlier_turns(history or [], question):
            facts["follow_up"] = True
        scoped = _scoped_item(plan, scope_item_id)
        if scoped is not None:
            facts["item"] = scoped.model_dump(mode="json", exclude=_ITEM_SCHEDULE_FIELDS)
    if re.search(WEATHER_WORDS, lower):
        target = _mentioned_date(question, plan)
        days = [day for day in plan.days if target is None or day.date == target]
        if target is None and _named_date(question, plan) is not None:
            # A date the trip does not cover: the plan does not hold that forecast,
            # and the other days' forecasts are not an answer to it.
            days = []
        weather = [
            day.weather.model_dump(mode="json")
            for day in days
            if day.weather is not None
        ]
        if weather:
            facts["weather"] = weather
    if re.search(r"\b(hotel|stay|room|check[- ]?in|check[- ]?out)\b", lower) and plan.hotels:
        facts["hotel"] = plan.hotels[0].model_dump(mode="json")
    if re.search(r"\b(flight|fly|train|ticket|depart|arrive)\b", lower) and plan.tickets:
        facts["tickets"] = [ticket.model_dump(mode="json") for ticket in plan.tickets]
    if re.search(r"\b(budget|cost|total|spend|price)\b", lower):
        facts["cost"] = plan.cost.model_dump(mode="json")

    # "how far", "closest", "nearest": answered from coordinates we already hold.
    distances = _distance_facts(plan, question, focus_item=_scoped_item(plan, scope_item_id))
    if distances:
        facts["distances"] = distances

    # A question about a section the plan does not have should name the reason
    # the source failed, not just say "the plan does not contain it".
    missing: list[str] = []
    if re.search(r"\b(hotel|stay|room|check[- ]?in)\b", lower) and not plan.hotels:
        missing.append("hotel")
    if re.search(r"\b(flight|fly|ticket|train|depart|arrive)\b", lower) and not plan.tickets:
        missing.append("tickets")
    if missing:
        wanted = {section.rstrip("s") for section in missing}
        facts["missing"] = {
            "sections": missing,
            "reasons": [
                gap.message
                for gap in plan.data_quality.gaps
                if gap.section.rstrip("s") in wanted
            ],
        }

    # An explicit item name wins, but collection questions ("what museums",
    # "list all attractions", "what did you change") get the whole itinerary so
    # the writer can answer from all current items rather than one guess.
    # A selected stay is its own scope: the hotel card in the plan is not a day
    # item, so the question gets the stay record (rate, rating, area, sources)
    # instead of an itinerary entry.
    scoped_hotel = plan.find_hotel(scope_item_id) if scope_item_id else None
    if scoped_hotel is not None:
        facts["hotel"] = scoped_hotel.model_dump(mode="json")
        facts["scope"] = {"kind": "hotel", "name": scoped_hotel.name}

    item = _find_item(plan, question, scope_item_id)
    if item and (
        scope_item_id
        or not re.search(
            r"\b(all|list|which|what|museums?|attractions?|restaurants?|meals?|activities|stops?)\b",
            lower,
        )
    ):
        facts["item"] = item.model_dump(mode="json", exclude=_ITEM_SCHEDULE_FIELDS)

    plan_words = re.search(
        r"\b(plan|itinerary|trip|day|days|museum|museums|attraction|attractions|"
        r"restaurant|restaurants|food|meal|meals|activity|activities|stop|stops|"
        r"added|changed|updated|include|included|list|which|what|show|tell|name|names)\b",
        lower,
    )
    if plan_words:
        facts["itinerary"] = _itinerary_rows(plan)
        matches = _matching_items(plan, lower)
        if matches is not None:
            facts["matching_items"] = matches

    change_words = re.search(r"\b(added|changed|updated|so far|last change|what did you|diff|edit)\b", lower)
    if history and change_words:
        recent = []
        for message in earlier_turns(history, question)[-8:]:
            role = getattr(message, "role", "")
            content = strip_fence_tags(str(getattr(message, "content", "")))[:500]
            meta = getattr(message, "meta", {}) or {}
            recent.append({"role": role, "content": content, "diff": meta.get("diff")})
        facts["recent_changes"] = recent

    if not facts and plan.destination.name.lower() in lower:
        facts["trip"] = {
            "destination": plan.destination.name,
            "dates": [plan.dates.start.isoformat(), plan.dates.end.isoformat()],
            "days": len(plan.days),
            "summary": plan.summary,
        }
    return facts


def _matching_items(plan: TripPlan, lower: str) -> list[dict] | None:
    """Return the concrete items a listing question is asking about.

    Returns ``None`` when the question is not a typed listing, and ``[]`` when
    it is typed but nothing matches — an empty list is an answer ("none").
    """
    if re.search(r"\b(museums?|medums?|museam|muesum)\b", lower):
        keyword = "museum"
    elif re.search(r"\b(restaurants?|food|meals?|eat|dining)\b", lower):
        keyword = "meal"
    elif re.search(r"\b(attractions?|sights?|see|visit)\b", lower):
        keyword = "attraction"
    else:
        return None
    found = []
    for day_index, day in enumerate(plan.days, start=1):
        for item in day.items:
            text = f"{item.name} {item.notes} {' '.join(item.tags)}".lower()
            if keyword == "meal" and item.type.value != "meal":
                continue
            if keyword == "attraction" and item.type.value != "attraction":
                continue
            if keyword == "museum" and keyword not in item.name.lower():
                continue
            found.append(
                {
                    "day": day_index,
                    "date": day.date.isoformat(),
                    "name": item.name,
                    "type": item.type.value,
                    "start": item.start,
                    "end": item.end,
                    "notes": item.notes[:240],
                }
            )
    return found


def _find_item(plan: TripPlan | None, question: str, scope_item_id: str | None = None):
    if not plan:
        return None
    if scope_item_id:
        found = plan.find_item(scope_item_id)
        if found:
            return found[1]
    lower = question.lower()
    for day in plan.days:
        for item in day.items:
            if item.name.lower() in lower:
                return item
    # Fall back to the longest shared token sequence.
    best = None
    best_score = 0
    words = {word for word in re.findall(r"[a-z]{4,}", lower)}
    for day in plan.days:
        for item in day.items:
            score = len(words & {word for word in re.findall(r"[a-z]{4,}", item.name.lower())})
            if score > best_score:
                best, best_score = item, score
    return best if best_score >= 2 else None


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_NAMES = "|".join(_MONTHS)


def _mentioned_date(question: str, plan: TripPlan | None) -> date | None:
    """The plan day the question names ("day 2", "12 April"), if the plan has that day."""
    if not plan:
        return None
    lower = question.lower()
    day_match = re.search(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", lower)
    if day_match:
        index = int(day_match.group(1) or day_match.group(2))
        if 1 <= index <= len(plan.days):
            return plan.days[index - 1].date
    named = _named_date(question, plan)
    if named is not None:
        for day in plan.days:
            if day.date == named:
                return day.date
    return None


def _named_date(question: str, plan: TripPlan | None) -> date | None:
    """A calendar date the question names ("12 April", "April 12"), in or out of the trip.

    The year is the one that puts the date closest to the trip.
    """
    if not plan:
        return None
    lower = question.lower()
    match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\b", lower)
    if match:
        day_number, month = int(match.group(1)), _MONTHS[match.group(2)]
    else:
        match = re.search(rf"\b({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lower)
        if not match:
            return None
        day_number, month = int(match.group(2)), _MONTHS[match.group(1)]
    anchor = plan.dates.start
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(date(year, month, day_number))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda value: abs((value - anchor).days))


#: words that make a question about this trip; "what", "which", "show" alone do not
PLAN_TOPIC_WORDS = (
    r"\b(plan|itinerary|trip|day|days|museum|museums|attraction|attractions|"
    r"restaurant|restaurants|food|meal|meals|activity|activities|stop|stops|"
    r"added|changed|updated|include|included)\b"
)


def _relates_to_plan(
    question: str, plan: TripPlan | None, facts: dict, scope_item_id: str | None = None
) -> bool:
    """Whether the question is about this trip (Proposal p3 l.110-111).

    A date outside the trip is not; a selected stop or stay, the destination, a
    plan fact the question matched (weather, hotel, a named stop...) or a word
    about the itinerary is. The itinerary rows a bare "what" attaches do not count.
    """
    if plan is None:
        return False
    named = _named_date(question, plan)
    if named is not None and not (plan.dates.start <= named <= plan.dates.end):
        return False
    if scope_item_id and (plan.find_item(scope_item_id) or plan.find_hotel(scope_item_id)):
        return True
    lower = question.lower()
    if plan.destination.name.lower() in lower or set(facts) - {"itinerary"}:
        return True
    return bool(re.search(PLAN_TOPIC_WORDS, lower))


def _destination_geo(plan: TripPlan | None, context: TripContext):
    from app.domain.models import GeoLocation

    if plan and plan.destination.geo:
        return GeoLocation(name=plan.destination.name, geo=plan.destination.geo)
    return None


def _two_points(plan: TripPlan | None, question: str):
    if not plan:
        return None
    if plan.hotels and plan.hotels[0].geo:
        first = plan.hotels[0].geo
        for day in plan.days:
            for item in day.items:
                if item.geo:
                    return first, item.geo
    points = [item.geo for day in plan.days for item in day.items if item.geo]
    if len(points) >= 2:
        return points[0], points[1]
    return None


def _itinerary_rows(plan: TripPlan) -> list[dict[str, Any]]:
    """The whole plan, shaped for the writer: every day, every stop."""
    return [
        {
            "day": index + 1,
            "date": day.date.isoformat(),
            "title": day.title,
            "weather": day.weather.model_dump(mode="json") if day.weather else None,
            "items": [
                {
                    "name": entry.name,
                    "type": entry.type.value,
                    "start": entry.start,
                    "end": entry.end,
                    "notes": entry.notes[:240],
                    "status": entry.data_status.value,
                    "tags": entry.tags,
                }
                for entry in day.items
            ],
        }
        for index, day in enumerate(plan.days)
    ]


def _reference_point(plan: TripPlan, lower: str):
    """Where a "how far / closest" question measures from.

    The hotel when the plan has one, otherwise the destination centre. The
    answer is labelled with whatever was used, so a straight-line estimate is
    never presented as a hotel-to-door distance.
    """
    if plan.hotels and plan.hotels[0].geo:
        return f"the hotel ({plan.hotels[0].name})", plan.hotels[0].geo
    if re.search(r"\bairport\b", lower):
        return f"the {plan.destination.name} city centre (airport distance is not stored)", plan.destination.geo
    return f"the {plan.destination.name} city centre", plan.destination.geo


def _distance_facts(plan: TripPlan, question: str, focus_item=None) -> dict | None:
    """Straight-line distances from a reference point to every stop with a coordinate."""
    from app.infrastructure.http import haversine_km

    lower = question.lower()
    if not re.search(DISTANCE_WORDS, lower):
        return None
    reference_name, reference = _reference_point(plan, lower)
    if reference is None or reference.lat is None or reference.lng is None:
        return None
    # Say plainly when the reference is a stand-in, so a city-centre figure is
    # never read as a hotel-to-door walking distance.
    if plan.hotels and plan.hotels[0].geo:
        reference_name = f"the hotel ({plan.hotels[0].name})"
    else:
        reference_name = f"{reference_name} (no hotel in the plan yet)"

    rows: list[dict[str, Any]] = []
    for day_index, day in enumerate(plan.days, start=1):
        for item in day.items:
            if not item.geo or item.geo.lat is None or item.geo.lng is None:
                continue
            rows.append(
                {
                    "day": day_index,
                    "name": item.name,
                    "type": item.type.value,
                    "km": round(haversine_km(reference.lat, reference.lng, item.geo.lat, item.geo.lng), 1),
                }
            )
    if not rows:
        return None
    rows.sort(key=lambda row: row["km"])
    named = [row for row in rows if row["name"].lower() in lower]
    focus = None
    if focus_item is not None:
        focus = next((row for row in rows if row["name"] == focus_item.name), None)
    return {
        "reference": reference_name,
        "method": "straight-line estimate",
        "closest": rows[:3],
        "named": named or ([focus] if focus else []),
        "focus": focus,
    }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _place_detail(rows, name: str) -> dict | None:
    """The richest row the place source returned for this stop."""
    wanted = _norm(name)
    chosen = None
    for row in rows or []:
        data = row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row)
        if chosen is None:
            chosen = data
        actual = _norm(str(data.get("name") or ""))
        if wanted and actual and (actual == wanted or wanted in actual or actual in wanted):
            chosen = data
            break
    if chosen is None:
        return None
    reviews = [
        {
            "rating": review.get("rating"),
            "text": str(review.get("text") or "")[:240],
        }
        for review in (chosen.get("reviews") or [])
        if len(str(review.get("text") or "")) >= 40
    ][:3]
    return {
        "name": chosen.get("name") or name,
        "rating": chosen.get("rating"),
        "review_count": chosen.get("review_count"),
        # what visitors actually wrote, so an opinion question has evidence
        "reviews": reviews,
        "summary": str(chosen.get("description") or "")[:600],
        "address": chosen.get("address") or "",
        "price_level": chosen.get("price_level") or "",
        "website": chosen.get("website") or "",
        "sources": chosen.get("source_refs") or [],
    }


#: function words that add nothing to a keyword search
_QUERY_STOPWORDS = {
    "what", "whats", "where", "when", "which", "who", "why", "how", "is", "are", "was",
    "the", "like", "in", "on", "at", "for", "of", "to", "a", "an", "does", "do", "can",
    "i", "me", "my", "tell", "about", "with", "and", "or", "it", "its", "that", "this",
    "there", "any", "good", "great", "best", "please", "would", "should", "could",
}


def _cuisine_word(question: str) -> str | None:
    match = re.search(
        r"\b(japanese|sushi|ramen|chinese|korean|thai|italian)\b",
        question or "",
        re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def _cuisine_from_pages(question: str, rows: list, name: str) -> str:
    """Say only what the pages state. A missing cuisine is not a yes."""
    word = _cuisine_word(question) or "that cuisine"
    place = name or "that restaurant"
    hints = (word, "sushi", "ramen", "izakaya") if word == "japanese" else (word,)
    blob_of = lambda row: f"{row.get('title', '')} {row.get('summary', '')} {row.get('snippet', '')}".lower()
    hit = next((row for row in rows if any(hint in blob_of(row) for hint in hints)), None)
    if hit is None:
        return (
            f"I checked public pages for {place}. None of them state that it serves {word} food. "
            "I will not guess."
        )
    body = (hit.get("summary") or hit.get("snippet") or "").strip()
    url = hit.get("url") or ""
    return f"A public page mentions {word} food in connection with {place}: {hit.get('title')} — {body[:220]} {url}".strip()


def _keyword_query(question: str, destination: str) -> str:
    """Turn a sentence into the keywords a search engine expects.

    "what's the nightlife like in Hong Kong?" becomes "nightlife hong kong";
    feeding the sentence back verbatim returned unrelated pages.
    """
    words = re.sub(r"[^a-z0-9\s]", " ", (question or "").lower()).split()
    keep = [word for word in words if word not in _QUERY_STOPWORDS and len(word) > 2]
    query = " ".join(keep) or re.sub(r"\s+", " ", question or "").strip()
    if destination and destination.lower() not in query.lower():
        query = f"{query} {destination}".strip()
    return query


def _fallback_answer(facts: dict, source: str) -> str:
    if "web_answer" in facts:
        block = facts["web_answer"] or {}
        text = str(block.get("text") or "").strip()
        sources = block.get("sources") or []
        if text:
            return text + (f" Sources: {', '.join(sources[:4])}" if sources else "")
        return "The synthesis search returned no answer."
    if "web" in facts:
        rows = facts["web"] or []
        if not rows:
            return "No web result came back for that question."
        lines = []
        for row in rows[:3]:
            body = (row.get("summary") or row.get("snippet") or "").strip()
            lines.append(f"{row.get('title')} — {body[:180]}".strip())
        return "Web results — " + " | ".join(lines) + f" ({source})."
    if "place_detail" in facts:
        detail = facts["place_detail"]
        if not detail:
            return "The place source did not return any detail for that stop."
        bits: list[str] = []
        rating = detail.get("rating")
        reviews = detail.get("review_count")
        if rating is not None:
            bits.append(f"rated {rating}" + (f" from {reviews:,} reviews" if reviews else ""))
        if detail.get("summary"):
            bits.append(detail["summary"])
        if detail.get("price_level"):
            bits.append(f"price level {detail['price_level']}")
        if not bits:
            return (
                f"{detail.get('name')} is in the plan, but the place source returned "
                "no rating or summary for it."
            )
        return f"{detail.get('name')} — " + " — ".join(bits) + f" ({source})."
    if "missing" in facts:
        missing = facts["missing"]
        sections = ", ".join(missing.get("sections") or [])
        reasons = " ".join(missing.get("reasons") or [])
        if reasons:
            return f"The plan has no {sections} because the source did not answer: {reasons}"
        return f"The plan has no {sections} yet."
    if "distances" in facts:
        block = facts["distances"]
        rows = block.get("named") or block.get("closest") or []
        if rows:
            head = rows[0]
            return (
                f"{head['name']} is about {head['km']} km from {block['reference']} "
                f"({block.get('method', 'straight-line estimate')})."
            )
        focus = block.get("focus")
        if focus:
            return (
                f"{focus['name']} is about {focus['km']} km from {block['reference']} "
                f"({block.get('method', 'straight-line estimate')})."
            )
    if "weather" in facts:
        rows = facts["weather"]
        parts = []
        for row in rows[:4]:
            if row.get("status") == "ok" and row.get("high_c") is not None:
                low = row.get("low_c")
                parts.append(
                    f"{row.get('date')}: {row.get('summary')}, {round(row.get('high_c'))}°"
                    + (f" / {round(low)}°" if low is not None else "")
                )
            else:
                parts.append(f"{row.get('date')}: no forecast available")
        return "Forecast — " + "; ".join(parts) + f" ({source})."
    if "matching_items" in facts:
        rows = facts["matching_items"]
        if not rows:
            return "No items in the current itinerary match that. I can add one if you tell me which day."
        return "Here is what matches in the current plan — " + "; ".join(
            f"Day {row['day']}, {row['start']}–{row['end']}: {row['name']}" for row in rows[:20]
        ) + "."
    if "recent_changes" in facts:
        changes = [
            row for row in facts["recent_changes"]
            if row.get("role") == "assistant" and "Updated to version" in str(row.get("content", ""))
        ]
        if changes:
            return changes[-1]["content"]
    if "itinerary" in facts:
        lines = []
        for day in facts["itinerary"][:8]:
            names = [entry["name"] for entry in day["items"] if entry["type"] != "meal"]
            meals = [entry["name"] for entry in day["items"] if entry["type"] == "meal"]
            if names or meals:
                lines.append(
                    f"Day {day['day']}: " + ", ".join(names)
                    + (f" (meals: {', '.join(meals)})" if meals else "")
                )
        if lines:
            return "The current itinerary is — " + "; ".join(lines) + "."
    if "hotel" in facts:
        hotel = facts["hotel"]
        price = hotel.get("price_per_night") or {}
        rate = f"{price.get('currency', '')} {price.get('amount', '')}" if price else "no rate"
        return f"{hotel.get('name')} is the selected hotel. Reference rate: {rate} per night ({source})."
    if "tickets" in facts:
        ticket = facts["tickets"][0]
        return (
            f"The current option is {ticket.get('name')} from {ticket.get('origin')} to "
            f"{ticket.get('destination')} ({source}). Confirm the fare with the airline."
        )
    if "cost" in facts:
        cost = facts["cost"]
        return f"Known costs total {cost.get('currency')} {cost.get('total')}. Meals and attraction tickets are not costed."
    if "item" in facts:
        item = facts["item"]
        return f"{item.get('name')}: {item.get('notes') or 'no further detail was returned.'}"
    if "tool" in facts:
        if isinstance(facts["tool"], dict) and facts["tool"].get("unavailable"):
            return str(facts["tool"]["unavailable"])
        return f"The source returned: {json.dumps(facts['tool'], default=str)[:600]}"
    return "The current itinerary does not contain that."
