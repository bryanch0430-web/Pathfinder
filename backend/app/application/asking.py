"""Quick-question path: answer from the plan first, at most one allowlisted tool."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from app.application.llm_gateway import LLMGateway
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import TripContext, TripPlan
from app.security import fence_user_message

WEATHER_WORDS = r"\b(weather|rain|forecast|temperature|hot|cold|typhoon)\b"
DISTANCE_WORDS = r"\b(distance|how far|travel time|how long|get to|walk)\b"
HOURS_WORDS = r"\b(open|opening|hours|close|closed)\b"


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

        facts = _plan_facts(question, plan, history, scope_item_id) if plan else {}
        related = bool(plan and (plan.destination.name.lower() in question.lower() or facts))
        lower = question.lower()
        needs_live_tool = (
            (re.search(WEATHER_WORDS, lower) and "weather" not in facts)
            or (re.search(DISTANCE_WORDS, lower) and "route" not in facts)
            or (re.search(HOURS_WORDS, lower) and "item" not in facts)
        )
        if facts and not needs_live_tool:
            text = await self._phrase(question, plan, facts, "plan")
            return text, related, "plan"

        tool_name, tool_result, source = await self._one_tool(question, plan, context, toolbox)
        if tool_result is not None:
            text = await self._phrase(question, plan, {"tool": tool_result}, source or tool_name)
            return text, related, source or tool_name
        if facts:
            text = await self._phrase(question, plan, facts, "plan")
            return text, related, "plan"
        text = (
            "The current itinerary does not contain that, and no configured source answered it. "
            "I can look it up if you point me at a place or date."
        )
        return text, related, "none"

    async def _one_tool(
        self,
        question: str,
        plan: TripPlan | None,
        context: TripContext,
        toolbox: Toolbox,
    ) -> tuple[str, Any, str]:
        lower = question.lower()
        if re.search(WEATHER_WORDS, lower) and self.registry.weather:
            target_date = _mentioned_date(question, plan)
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
                        return "weather", None, "weather"
                    result = await toolbox.call(
                        "weather.forecast",
                        source.forecast,
                        geo=location,
                        start=start,
                        end=end,
                    )
                    return "weather", [row.model_dump(mode="json") for row in result], source.name
                except Exception:
                    return "weather", None, "weather"
        if re.search(DISTANCE_WORDS, lower) and self.registry.routes:
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
                    "sources": [ref.model_dump(mode="json") for ref in item.source_refs],
                }, "plan"
        return "none", None, "none"

    async def _phrase(
        self,
        question: str,
        plan: TripPlan | None,
        facts: dict,
        source: str,
    ) -> str:
        if self.gateway.available:
            user = (
                f"Question: {question}\n"
                f"Current plan summary: {json.dumps(plan.summary_for_prompt()) if plan else 'none'}\n"
                f"Facts for this answer: {json.dumps(facts, default=str)[:12000]}\n"
                f"Source: {source}\n\n"
                + fence_user_message(question)
            )
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


def _plan_facts(
    question: str,
    plan: TripPlan,
    history: list[Any] | None = None,
    scope_item_id: str | None = None,
) -> dict:
    lower = question.lower()
    facts: dict[str, Any] = {}
    if re.search(WEATHER_WORDS, lower):
        target = _mentioned_date(question, plan)
        days = [day for day in plan.days if target is None or day.date == target]
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
        facts["item"] = item.model_dump(mode="json")

    plan_words = re.search(
        r"\b(plan|itinerary|trip|day|days|museum|museums|attraction|attractions|"
        r"restaurant|restaurants|food|meal|meals|activity|activities|stop|stops|"
        r"added|changed|updated|include|included|list|which|what|show|tell|name|names)\b",
        lower,
    )
    if plan_words:
        facts["itinerary"] = [
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
        matches = _matching_items(plan, lower)
        if matches is not None:
            facts["matching_items"] = matches

    change_words = re.search(r"\b(added|changed|updated|so far|last change|what did you|diff|edit)\b", lower)
    if history and change_words:
        recent = []
        for message in history[-8:]:
            role = getattr(message, "role", "")
            content = str(getattr(message, "content", ""))[:500]
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


def _mentioned_date(question: str, plan: TripPlan | None) -> date | None:
    if not plan:
        return None
    lower = question.lower()
    day_match = re.search(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", lower)
    if day_match:
        index = int(day_match.group(1) or day_match.group(2))
        if 1 <= index <= len(plan.days):
            return plan.days[index - 1].date
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        lower,
    )
    if match:
        month = months[match.group(2)]
        for day in plan.days:
            if day.date.month == month and day.date.day == int(match.group(1)):
                return day.date
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})\b",
        lower,
    )
    if match:
        month = months[match.group(1)]
        for day in plan.days:
            if day.date.month == month and day.date.day == int(match.group(2)):
                return day.date
    return None


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


def _fallback_answer(facts: dict, source: str) -> str:
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
        return f"The source returned: {json.dumps(facts['tool'], default=str)[:600]}"
    return "The current itinerary does not contain that."
