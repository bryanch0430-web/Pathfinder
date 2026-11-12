"""Minimal-edit path.

The router names the affected parts. Only the agents that own those parts run
again, unlocked stops keep their data, confirmed items are preserved, and the
version increments by one.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from pydantic import BaseModel, Field

from app.application.agents import MAX_PLACE_CANDIDATES, _same_place, place_candidate_limit
from app.application.freshness import outdated_sections
from app.application.language import cuisine_slot
from app.application.opening_hours import lines_for, only_other_weekdays, windows_on
from app.application.plan_focus import day_number_named
from app.application.planning import PlanPath
from app.application.planner import (
    NO_STOPS_NOTE,
    PACE_BUFFER_MINUTES,
    STAY_PRICE_NOT_REFRESHED,
    Planner,
    _constraints,
    _pace,
    _reservations,
)
from app.application.prompt_context import conversation_block, plan_outline, trip_slots, with_conversation
from app.application.router import (
    GRANDER_WORDS,
    MODIFY_VERBS,
    affected_parts_for,
    asks_for_new_dates,
    asks_for_trip_dates,
    is_replan_message,
    looks_like_question,
    without_concessive_budget,
)
from app.application.toolbox import Toolbox
from app.application.transfers import LATEST_END, refresh_transfers, with_transfer_note
from app.application.trip_rules import blocked_dates, travel_windows
from app.application.validation import diff_plans, is_edit_outcome, repair_plan, tidy_data_gaps, validate_plan
from app.config import Settings
from app.domain.errors import TOOL_LIMIT_MESSAGE, CanaryLeak, PlanInvalidError
from app.domain.models import (
    AgentName,
    ConstraintCheck,
    DataStatus,
    DayPlan,
    Disruption,
    GeoLocation,
    GeoPoint,
    HotelStay,
    ItemType,
    Message,
    Money,
    PlaceCandidate,
    PlanItem,
    PriceKind,
    SourceRef,
    TicketOption,
    TripContext,
    TripPlan,
    WeatherDay,
    new_id,
    utcnow,
)
from app.domain.place_names import trusted_names
from app.domain.ports import Tracer
from app.infrastructure.http import haversine_km
from app.security import fence_user_message, replayable, strip_fence_tags

#: said on the plan when the trip is moved to dates that have gone by (the form accepts them)
PAST_DATES = "The new dates are in the past."
#: the hotel lookup, as the toolbox names it when the per-turn cap refuses it
HOTEL_SEARCH = "hotels.search"
#: the place lookup, as the toolbox names it when the per-turn cap refuses it
PLACES_SEARCH = "places.search"
#: said (and then not kept: it is an edit outcome) when a request for another or a cheaper hotel found none
NO_CHEAPER_HOTEL = "No cheaper verified hotel was returned; the current hotel stays."
#: said (and then not kept: it is an edit outcome) when the hotel selected or named could not be swapped and the
#: request did not ask for a cheaper one ("help me change the hotel")
NO_OTHER_HOTEL = "No other verified hotel within the budget was returned; the current hotel stays."
#: said when the stay an edit put in the plan came with no price, so the total leaves it out
NEW_STAY_UNPRICED = "The new hotel's price was not returned, so the total does not include the stay."
#: the flight of the earlier dates is gone from a trip that moved, and none came back for the new ones
FLIGHT_NO_LONGER_FITS = "The earlier flight no longer fits the new dates and no flight for them came back."
#: said (and then not kept: it is an edit outcome) when a request for a grander hotel found none the budget pays for
NO_GRANDER_HOTEL = "No grander verified hotel within the budget was returned; the current hotel stays."
#: the page's "generate again" when the trip form has nothing the plan does not
NOTHING_DIFFERS = "Nothing in the trip details differs from the plan, so nothing was changed."
#: the fares are for the trip as it was planned when the source has none for the details the traveller changed
FARE_KEPT = "No flight for the new trip details came back; the earlier fare is kept."
#: the stay's rate is for the party it was planned for when the source has none for the new party size
STAY_RATE_KEPT = "No hotel price for the new trip details came back; the earlier rate is kept."
#: the answer to a request for other dates when the form holds the plan's dates: the trip form is where dates change
CHAT_DATES_REPLY = (
    "I cannot read new dates from a chat message. Change the start and end dates in the trip details, then "
    "generate the itinerary again; the plan is unchanged."
)
#: the reply to an edit that changed nothing and found nothing new to say
NOTHING_MATCHED = "Nothing in the plan matched that request, so it is unchanged."

AGENT_SECTION: dict[AgentName, str] = {
    AgentName.ATTRACTION: "attractions",
    AgentName.HOTEL: "hotels",
    AgentName.WEATHER: "weather",
    AgentName.TICKET: "tickets",
}

PART_AGENTS: dict[str, set[AgentName]] = {
    "hotel": {AgentName.HOTEL},
    "budget": {AgentName.HOTEL},
    "attraction": {AgentName.ATTRACTION},
    "ticket": {AgentName.TICKET},
    "weather": {AgentName.WEATHER},
    "dates": {AgentName.WEATHER, AgentName.HOTEL, AgentName.TICKET},
}


class ChangeDraft(BaseModel):
    description: str = ""
    affected_parts: list[str] = Field(default_factory=list)
    target_day: int | None = None
    target_item_id: str | None = None
    new_values: dict[str, Any] = Field(default_factory=dict)
    is_question: bool = False
    confidence: float = 0.0
    #: a fact the change needs that neither the plan nor the conversation gives ("where the traveller lives")
    missing_info: str | None = None
    #: the one short question that asks for it
    question: str | None = None


#: the question for a home the traveller asked to add without saying where it is
HOME_QUESTION = "Where do you live? I'll add it to the plan."
#: the question for an edit the writer could not read and nothing on the page points at
WHICH_EDIT_QUESTION = "Which stop or day should I change, and what should I change about it?"
#: below this the writer's own confidence in its reading of an edit is too low to act on
LOW_EDIT_CONFIDENCE = 0.4
#: the fact `HOME_QUESTION` asks for, as the stored question turn names it
HOME_FACT = "home"
#: asked once more, in other words, when the answer to `HOME_QUESTION` was not a place
HOME_AGAIN_QUESTION = "Could you give the area or the address where you live? I'll add it to the plan."
#: the fact `HOME_AGAIN_QUESTION` asks for: an answer that is again not a place drops the edit
HOME_AGAIN_FACT = "home-again"
HOME_FACTS = frozenset({HOME_FACT, HOME_AGAIN_FACT})
#: the reply when the traveller turns the question down, or still gives no place after it was asked twice
LEFT_AS_IT_WAS = "Understood, I left the plan as it was."


@dataclass(frozen=True)
class EditQuestion:
    """The one question an edit asks for a fact it needs, instead of changing the plan."""

    question: str
    #: what the question asks for ("home"), so the answer on the next turn can be read as that fact
    fact: str = ""
    #: the edit the answer completes
    request: str = ""


class EditResult(tuple):
    """What an edit returns: the plan and the diff, as a pair, with the scope the edit really used.

    ``scope`` is "stop" (one row, selected or named), "day" (more stops on one day) or "plan"; ``day`` is the day of a
    day-scoped edit (1-based) and ``selected`` says whether the stop or day came from the stop selected on the page.
    ``asks`` is set when the edit changed nothing because it needs a fact first: the plan is the one it read, with the
    question as its change summary, so a caller that does not ask still shows the question as the reply."""

    scope: str
    day: int | None
    selected: bool
    asks: EditQuestion | None

    def __new__(
        cls,
        plan: TripPlan,
        diff: list[dict[str, Any]],
        *,
        scope: str = "plan",
        day: int | None = None,
        selected: bool = False,
        asks: EditQuestion | None = None,
    ):
        result = super().__new__(cls, (plan, diff))
        result.scope, result.day, result.selected, result.asks = scope, day, selected, asks
        return result


class ModifyPath:
    def __init__(self, *, planner: Planner, plan_path: PlanPath, settings: Settings, gateway: Any):
        self.planner = planner
        self.plan_path = plan_path
        self.settings = settings
        self.gateway = gateway

    async def run(
        self,
        *,
        plan: TripPlan,
        context: TripContext,
        message: str,
        route_parts: list[str],
        toolbox: Toolbox,
        tracer: Tracer,
        emit,
        memory_summaries: list[str] | None = None,
        scope_item_id: str | None = None,
        history: Sequence[Message] = (),
        answering: EditQuestion | None = None,
    ) -> EditResult:
        """Apply the smallest change the message asks for. ``answering`` is the question an earlier edit asked when the
        message is its answer: the edit it completes is read with the answer (the writer reads it in the conversation).
        When a fact the change needs is missing, the result `asks` for it and the plan is unchanged."""
        # The trip form may hold dates the plan does not have yet (it was saved, not generated). Only a sentence
        # that asks for other dates, or the page's "generate again", moves the trip to them: "day 2", "the last
        # two nights" or "a later flight" name a part of the trip (Proposal DB-09), not new dates.
        try:
            new_range = context.date_range()
        except ValueError:  # pydantic's ValidationError: the form holds an end before its start, or more than 60 days
            new_range, form_is_a_trip = None, False
        else:
            form_is_a_trip = True
        # The page's "generate again" is a request to plan again with the trip details: what the form has that the plan
        # does not (apart from its dates, above) is what changes.
        replanning = is_replan_message(message)
        form_changes = _form_differences(plan, context) if replanning else []
        if replanning and form_is_a_trip and new_range == plan.dates and not form_changes:
            # The page's own message needs no writer to be read when the form holds nothing the plan does not.
            return _nothing_differs(plan)
        draft = await self._extract(message, plan, context, route_parts, history=history)
        answer = message
        if answering is not None and answering.request and not _imperative_edit(message):
            # The message answers the question an earlier edit asked ("Mong Kok" to "Where do you live?"): the rules
            # below read that edit together with its answer. The writer read both in the conversation.
            message = f"{answering.request} ({message.strip()})"
        asked_for_home = answering is not None and answering.fact in HOME_FACTS
        home = _stated_home(draft, answer, history, asked_for_home=asked_for_home)
        # The home is what the edit adds ("add the place I live", the answer to "Where do you live?"), or only where
        # something else is ("add a cafe near my home in Mong Kok"): then the other thing is added and the home noted.
        home_is_the_add = asked_for_home or _adds_the_home(message) or (home is not None and _only_says_the_home(answer))
        if home is not None and home_is_the_add and not replanning:
            return _home_noted(plan, home)
        if answering is not None and _DISMISSAL.search(answer) and not _imperative_edit(answer):
            # "No" or "never mind" to the question: the edit it belonged to is dropped, and nothing is asked again.
            return _left_as_it_was(plan)
        if asked_for_home and home is None and not (_imperative_edit(answer) or looks_like_question(answer)):
            # "yes", "I'm not sure": not a place. Asked once more, in other words; after that the edit is dropped.
            if answering.fact == HOME_FACT:
                unchanged = plan.model_copy(deep=True)
                unchanged.change_summary = HOME_AGAIN_QUESTION
                return EditResult(
                    unchanged, [], asks=EditQuestion(HOME_AGAIN_QUESTION, fact=HOME_AGAIN_FACT, request=answering.request)
                )
            return _left_as_it_was(plan)
        noted_home = home
        more_stops = asks_for_more_stops(message)
        # "Make it grander": the hotel's class, an upgrade the budget can pay for, never the pace of the days.
        # The router stays the authority on scope: a router that named other parts than the hotel is not overruled.
        # A router that named only the attractions for a sentence that names none ("can u make it more grrand") did
        # not see the hotel in it; a bare "more grand" with attractions named stays the router's call.
        upscale = (
            not more_stops
            and asks_for_a_grander_stay(message)
            and (
                not route_parts
                or bool({"hotel", "budget"} & set(route_parts))
                or (set(route_parts) <= {"attraction"} and _grander_without_a_stop(message))
            )
        )
        if (more_stops or upscale) and not interpret_plan(message).requested:
            # "More stops" is an add, and "grander" is about the hotel. A pace the writer read into either ("3 places
            # a day") is only a cap, which adds nothing and may even take a stop away.
            draft.new_values = {
                key: value for key, value in draft.new_values.items() if key not in {"places_per_day", "min_hours", "earliest"}
            }
        shape = interpret_plan(message, draft.new_values)
        date_requested = not shape.requested and (is_replan_message(message) or asks_for_new_dates(message))
        if date_requested and not form_is_a_trip:
            raise PlanInvalidError(
                "The dates in the trip details do not make a trip: the end must not be before the start, and a trip "
                "is at most 60 days long. Correct them in the trip details and ask again."
            )
        dates_move = bool(new_range and new_range != plan.dates) and date_requested
        if replanning and not dates_move and not form_changes:
            return _nothing_differs(plan)
        # A request for the dates or the length of the trip is about the whole trip, whatever row is selected or named
        # in it ("keep this hotel but move to March", "make it 4 days, Senso-ji must stay"). A bare span ("two days
        # later") is about the trip too, unless it is said of a row: "it", "this" or a name in the plan.
        whole_trip = date_requested and (
            is_replan_message(message)
            or asks_for_trip_dates(message)
            or not (re.search(r"\b(?:it|this|that|these|those|them)\b", message.lower()) or _name_mentioned(plan, message))
        )
        if dates_move or whole_trip:
            # A request for the whole trip is not about the stop the traveller has selected.
            scope_item_id = None
        if dates_move:
            draft.is_question = False
        # More stops for a day: the day the message names, else the day of the stop it names or the page selected
        # ("can u add this days more stop" with "Day 9 · Lam Tsuen Wishing Tree" selected is about Day 9).
        more_stops = more_stops and not (dates_move or whole_trip)
        day_scope: int | None = None
        day_from_selection = False
        if more_stops:
            named_day = _rule_day(message)
            anchor = _name_mentioned(plan, message) or scope_item_id
            anchored = plan.find_item(anchor) if anchor else None
            if named_day and 1 <= named_day <= len(plan.days):
                day_scope = named_day
            elif anchored is not None:
                day_scope = plan.days.index(anchored[0]) + 1
                day_from_selection = anchor == scope_item_id
            draft.target_item_id = None
            draft.is_question = False
        if scope_item_id and not more_stops and not draft.target_item_id and plan.find_item(scope_item_id):
            # The console's "About: Day 3 · X" chip is a real context signal.
            # "remove this" / "swap it" must resolve to that stop, and a real
            # imperative with a selected item is a change even if the writer
            # calls the terseness a question.
            draft.target_item_id = scope_item_id
            if re.search(
                r"\b(remove|delete|drop|swap|replace|change|switch|make|add|include|move|eat|dinner|lunch|restaurant)\b",
                message.lower(),
            ):
                draft.is_question = False
        elif (
            scope_item_id
            and not more_stops
            and plan.find_hotel(scope_item_id)
            and _polite_request(message)
            and not _about_another_part(message)
            and not _stay_change_refused(
                _stay_as_word(message, plan.find_hotel(scope_item_id).name), selected_stay=True
            )
        ):
            # The hotel row selected is a selection too: "can change", "help me change the hotel" asks to swap it. The
            # selected stay is the row the edit refers to below, and the swap is the one "swap this hotel" takes.
            # "can I change the hotel later?" stays a question; "Add a cafe on day 2" is about day 2.
            draft.is_question = False
            if "hotel" not in draft.affected_parts:
                draft.affected_parts = [*draft.affected_parts, "hotel"]
        if not (dates_move or whole_trip or replanning):
            # An edit that needs a fact nobody gave ("add the place I live") asks for it, once, instead of refusing.
            asked = _missing_fact_question(
                plan,
                message,
                draft,
                scope_item_id,
                anchored=more_stops or upscale or shape.requested,
                home_wanted=noted_home is None and _adds_the_home(message),
            )
            if asked is not None:
                question, fact = asked
                unchanged = plan.model_copy(deep=True)
                unchanged.change_summary = question
                return EditResult(unchanged, [], asks=EditQuestion(question, fact=fact, request=message))
        if draft.is_question and _polite_request(message):
            # "can change the hotel", "help me change it", "pls swap this": a change verb and no "?" is a request,
            # however politely it is put (v1.3.0: only a message that ends in "?" without a change verb is an ask).
            # One with nothing to act on was asked about above.
            draft.is_question = False
            if _wants_another_stay(message) and "hotel" not in draft.affected_parts:
                # "don't move anything, find another hotel": the writer that called it a question named no part.
                draft.affected_parts = [*draft.affected_parts, "hotel"]
        if draft.is_question:
            raise PlanInvalidError(
                "This message reads as a question, not a change. Ask it as a question and I will answer "
                "without touching the plan."
            )
        affected = [part for part in draft.affected_parts if part in PART_AGENTS or part.startswith("day_")]
        if route_parts:
            # The router is the authority on scope. The writer may narrow it but
            # must not silently add an agent the router did not name.
            narrowed = [part for part in affected if part in route_parts or part.startswith("day_")]
            affected = narrowed or route_parts
        if form_changes:
            # No stop is meant by the page's message. The stay and the fares depend on who travels, from where, and on
            # the budget (the hotel search is capped by it).
            affected = [part for part in affected if part != "attraction"]
            if {"party", "origin", "budget"} & set(form_changes):
                affected += [part for part in ("hotel", "ticket") if part not in affected]
        if upscale and not form_changes and "hotel" not in affected:
            affected.append("hotel")
        if not affected and not form_changes:
            affected = ["dates"] if dates_move else ["attraction"]
        if re.search(r"\b(hours?|minutes?)\b", message.lower()) and not re.search(
            r"\b(cheap|cheaper|money|price|budget|hkd|usd|\$)\b", message.lower()
        ):
            affected = [part for part in affected if part != "budget"]
        # Weather, hotel and flights rerun for "dates" only when the trip's dates actually move (Proposal DB-09).
        agent_names: set[AgentName] = set()
        for part in affected:
            if part == "dates" and not dates_move:
                continue
            agent_names |= PART_AGENTS.get(part, {AgentName.ATTRACTION})
        if not agent_names and not form_changes:
            agent_names = {AgentName.ATTRACTION}
        # "even if it is over budget" grants leave to spend; it does not ask for a cheaper trip (router.py).
        unconceded = without_concessive_budget(message.lower())
        budget_mode = "budget" in affected or bool(
            re.search(
                r"\b(cheap|cheaper|optimis|optimiz|reduce|lower|afford|over budget|make it work)\b",
                unconceded,
            )
        )
        agent_names = lock_scope(
            agent_names,
            plan,
            message,
            scope_item_id,
        )
        if dates_move:
            agent_names |= PART_AGENTS["dates"]
            if "attraction" not in affected:
                agent_names.discard(AgentName.ATTRACTION)
        place = _stay_as_a_place(plan, message, scope_item_id)
        if place is not None:
            # "add a cafe near Kyoto Garden Inn" is about the cafe: the stay is "the hotel" from here on, so it is
            # neither the stop to edit nor, by the words of its name ("Garden"), a wish for a garden.
            message = _stay_as_word(message, place.name)
        # Naming a hotel or a stop in a request for other dates does not turn it into a single-row edit.
        # More stops are added to a day: the stop selected or named only says which day.
        referred = None if dates_move or whole_trip or more_stops else resolve_referred(plan, message, scope_item_id)
        if (
            referred is not None
            and referred == scope_item_id
            and plan.find_hotel(referred)
            and _name_mentioned(plan, message) is None
            and (
                _about_another_part(message)
                or (
                    _stay_change_refused(_stay_as_word(message, plan.find_hotel(referred).name), selected_stay=True)
                    and not _refuses_change(message)
                )
            )
        ):
            # The hotel is selected but the sentence is about another part or a day ("Add a cafe on day 2"), or it
            # refuses the hotel change and asks for another ("don't change the hotel, add a cafe on day 2"): the stay
            # is not the row it changes.
            referred = None
        # A pace or meal rule is about the whole plan. A selected chip must
        # not turn it into a question about one stop.
        if shape.requested and _name_mentioned(plan, message) is None:
            referred = None
            draft.is_question = False
            agent_names = set()
        if more_stops and not shape.requested:
            # New stops come from the place source, and nothing but that day changes.
            agent_names = {AgentName.ATTRACTION}
        if upscale and not shape.requested and not (referred and plan.find_hotel(referred)) and _name_mentioned(plan, message) is None:
            # A grander stay is about the hotel, whatever stop is selected: only the hotel search runs again.
            referred = None
            draft.is_question = False
            agent_names = {AgentName.HOTEL}
        if referred:
            # The traveller pointed at one row. No other row, hotel, or flight
            # may change, even if the router named a whole section.
            draft.target_item_id = referred
            draft.is_question = False
            agent_names = (
                {AgentName.HOTEL} if plan.find_hotel(referred) else {AgentName.ATTRACTION}
            )
        if budget_mode and context.budget is not None and not referred and not more_stops:
            # A budget request is not only a hotel request: the flight is often
            # the largest line, so both priced sections are re-run.
            agent_names.add(AgentName.HOTEL)
            agent_names.add(AgentName.TICKET)

        if budget_mode and not _has_prices(plan):
            # Nothing in the plan carries a price, so "make it cheaper" has
            # nothing to act on. Ask instead of re-running agents and answering
            # "no cheaper hotel was returned", which reads like a failure.
            scoped = _scoped_name(plan, scope_item_id)
            unchanged = plan.model_copy(deep=True)
            unchanged.change_summary = (
                "Nothing in this plan has a price yet — the hotel and flight sources "
                "returned no rates, and "
                + (f"{scoped} carries no price of its own. " if scoped else "no stop carries a price. ")
                + "Do you want me to replace "
                + (f"{scoped}" if scoped else "the selected stop")
                + " with another verified option, or keep the itinerary as it is?"
            )
            return unchanged, []

        old_plan = plan
        if new_range and new_range != plan.dates and not dates_move:
            # The conversation must not drift: only an explicit date change may
            # move the trip. Restore the plan's dates into the context.
            context.start_date = plan.dates.start
            context.end_date = plan.dates.end
            context.days = len(plan.dates.dates)
            new_range = plan.dates
        date_changed = dates_move
        # Only an edit that asks for another hotel or the budget may change which hotel it is. When the
        # dates move, the stay the traveller accepted is priced again for the new dates, not swapped.
        referred_stay = plan.find_hotel(referred) if referred is not None else None
        # The stay's own name stands for the word hotel ("keep Kyoto Garden Inn" keeps it like "keep the hotel").
        stay = referred_stay or (plan.hotels[0] if plan.hotels else None)
        about_stay = _stay_as_word(message, stay.name) if stay is not None else message
        budget_in_form = "budget" in form_changes
        # "don't change the hotel", "no need to change anything": a refused change of the stay asks for no other hotel.
        # A refusal aimed at something else does not ("don't remove any stops, I want a nicer hotel").
        change_refused = _stay_change_refused(
            about_stay, selected_stay=bool(scope_item_id and plan.find_hotel(scope_item_id))
        )
        hotel_requested = (
            not replanning
            and (
                "budget" in affected
                or budget_mode
                or upscale
                or (not change_refused and _asks_for_another_hotel(about_stay, affected))
                # The hotel selected or named is the one to swap, unless the sentence says to keep it or refuses the
                # change.
                or (referred_stay is not None and not _keeps_the_hotel(about_stay) and not change_refused)
            )
            # Another budget in the trip form gives a plan without a stay the search it had none from.
            or (budget_in_form and not plan.hotels)
        )
        # The stay is quoted for who travels and searched within the budget: another party size or budget asks the
        # hotel source again for the stay the plan has.
        requote_stay = ("party" in form_changes or budget_in_form) and bool(plan.hotels)
        if not (hotel_requested or date_changed or requote_stay):
            # Nothing asks for another hotel and no date or party size moves: the hotel agent has no work to do.
            agent_names.discard(AgentName.HOTEL)
        if date_requested and not date_changed and referred is None and not is_replan_message(message):
            elsewhere = {part for part in affected if not part.startswith("day_")} - {"dates"}
            if not hotel_requested:
                elsewhere.discard("hotel")
            if not elsewhere:
                # A sentence about the whole trip that no stored range backs: dates change in the trip details.
                answer = plan.model_copy(deep=True)
                answer.change_summary = CHAT_DATES_REPLY
                return answer, []
        cheaper = budget_mode or bool(
            re.search(r"\b(cheap|cheaper|budget|lower|reduce|less)\b", without_concessive_budget(message.lower()))
        )
        if date_changed and len(new_range.dates) != len(plan.days):
            # A different length is a different shape; rebuild with the same id and version + 1. The stay the
            # traveller has is kept unless they asked for another or a cheaper one, and then it is chosen as an edit
            # of the same length chooses it: another hotel, a lower rate for "cheaper", else the stay it had.
            asked = hotel_requested and bool(plan.hotels)
            rebuilt = await self.plan_path.run(
                context=context,
                session_id=plan.session_id,
                toolbox=toolbox,
                tracer=tracer,
                emit=emit,
                memory_summaries=memory_summaries,
                version=plan.version + 1,
                trip_id=plan.trip_id,
                change_summary=draft.description or "Changed the trip dates; rebuilt the itinerary.",
                keep_hotel=plan.hotels[0] if plan.hotels else None,
                pick_hotel=(lambda rows: _swap_choice(plan, rows, cheaper=cheaper, message=message)) if asked else None,
            )
            if (
                asked
                and rebuilt.hotels
                and rebuilt.hotels[0].name == plan.hotels[0].name
                and HOTEL_SEARCH not in getattr(toolbox, "refused", ())
            ):
                rebuilt.data_quality.gaps.append(_gap("hotels", NO_CHEAPER_HOTEL))
            elif asked and rebuilt.hotels and rebuilt.hotels[0].price_per_night is None:
                rebuilt.data_quality.gaps.append(_gap("hotels", NEW_STAY_UNPRICED))
            if new_range.start < date.today():
                rebuilt.data_quality.gaps.append(_gap("dates", PAST_DATES))
            if plan.tickets and not rebuilt.tickets and not any(g.section == "tickets" for g in rebuilt.data_quality.gaps):
                # A source that answered with no flight (and said nothing was wrong) still leaves the fare gone.
                rebuilt.data_quality.gaps.append(_gap("tickets", FLIGHT_NO_LONGER_FITS))
            rebuilt = await self.carry_protected(plan, rebuilt, context=context, toolbox=toolbox)
            return rebuilt, diff_plans(old_plan, rebuilt)

        destination = GeoLocation(
            name=plan.destination.name,
            country_code=plan.destination.country_code,
            timezone=plan.destination.timezone,
            geo=plan.destination.geo,
            source="current plan",
        )
        if date_changed:
            # The trip moved: its forecast, stay and flights are for other days now.
            agent_names |= PART_AGENTS["dates"]
        date_range = new_range or plan.dates
        lower_message = message.lower()
        add_requested = _ADD_REQUEST.search(lower_message) is not None or more_stops
        remove_requested = _REMOVE_REQUEST.search(lower_message) is not None
        # The agents whose data this edit needs (Proposal DB-09). A pace change
        # works from the plan alone, and taking a stop out needs no new places.
        needed = set(agent_names)
        if shape.requested and referred is None:
            needed = set()
        elif remove_requested and not add_requested:
            needed.discard(AgentName.ATTRACTION)
        fx = getattr(self.plan_path.registry, "fx", None)
        if fx is not None and (budget_mode or needed & {AgentName.HOTEL, AgentName.TICKET}):
            # Rates are needed only when a priced line (stay or flight) can change.
            try:
                with tracer.span("fx.refresh"):
                    await toolbox.call("fx.rates", fx.refresh)
            except Exception:
                pass
        if not needed:
            # run_agents treats an empty selection as "all agents".
            results = {}
        else:
            with tracer.span("modify.agents", parts=",".join(sorted(needed))):
                results = await self.plan_path.run_agents(
                    context=context,
                    destination=destination,
                    date_range=date_range,
                    toolbox=toolbox,
                    emit=emit,
                    names=needed,
                    extra=_search_extra(message) if AgentName.ATTRACTION in needed else None,
                )

        updated = plan.model_copy(deep=True)
        # A section that is rerun gets a fresh gap verdict. Without this, an old
        # Wikimedia/weather/ticket gap survives after the condition is gone.
        refreshed_sections = {AGENT_SECTION[name] for name in needed}
        updated.data_quality.gaps = [
            gap for gap in updated.data_quality.gaps if gap.section not in refreshed_sections
        ]
        # What each rerun agent could not fetch belongs on the plan, as it does on a new trip.
        for result in results.values():
            for gap in result.failures:
                if not any(known.section == gap.section and known.message == gap.message for known in updated.data_quality.gaps):
                    updated.data_quality.gaps.append(gap)
        if date_changed and new_range:
            # What the plan said about its earlier dates (that they were past) is not said of the new ones.
            updated.data_quality.gaps = [gap for gap in updated.data_quality.gaps if gap.section != "dates"]
            if new_range.start < date.today():
                updated.data_quality.gaps.append(_gap("dates", PAST_DATES))
            updated.dates = new_range
            for index, day in enumerate(updated.days):
                day.date = new_range.dates[index]
                if day.weather is not None and day.weather.date != day.date:
                    # That forecast is for the old dates. Without a new one the day shows none.
                    day.weather = None
            if updated.hotels:
                updated.hotels[0].check_in = new_range.start
                updated.hotels[0].check_out = new_range.end
            updated.tickets = [ticket for ticket in updated.tickets if _ticket_within(ticket, new_range)]
            updated.data_quality.gaps += [_gap("attractions", text) for text in _hours_after_a_move(updated)]

        if AgentName.HOTEL in agent_names and (referred is None or plan.find_hotel(referred)):
            hotels = results.get("hotel")
            candidates = (hotels.payload.get("hotels") if hotels else []) or []
            if updated.hotels and not hotel_requested:
                kept, refreshed = self.planner.keep_stay(updated.hotels[0], candidates, updated.dates)
                updated.hotels = [kept]
                if not refreshed:
                    updated.data_quality.gaps.append(
                        _gap("hotels", STAY_PRICE_NOT_REFRESHED if date_changed else STAY_RATE_KEPT)
                    )
            else:
                if upscale and not cheaper:
                    swapped = self._grander_stay(updated, candidates, context)
                else:
                    choice = _swap_choice(updated, candidates, cheaper=cheaper, message=message)
                    swapped = self.planner._build_hotel(choice, updated.dates) if choice else None
                # A lookup the cap refused returned nothing because it was never made: its gap says so, once.
                searched = HOTEL_SEARCH not in getattr(toolbox, "refused", ())
                if swapped is not None:
                    updated.hotels = [swapped]
                    if swapped.price_per_night is None:
                        updated.data_quality.gaps.append(_gap("hotels", NEW_STAY_UNPRICED))
                elif updated.hotels:
                    if date_changed:
                        # The hotel stays, but its rate is for the old dates: take the candidates' rate for the new.
                        kept, refreshed = self.planner.keep_stay(updated.hotels[0], candidates, updated.dates)
                        updated.hotels = [kept]
                        if not refreshed:
                            updated.data_quality.gaps.append(_gap("hotels", STAY_PRICE_NOT_REFRESHED))
                    if searched:
                        if upscale and not cheaper:
                            outcome = NO_GRANDER_HOTEL
                        elif referred_stay is not None and not cheaper:
                            outcome = NO_OTHER_HOTEL
                        else:
                            outcome = NO_CHEAPER_HOTEL
                        updated.data_quality.gaps.append(_gap("hotels", outcome))
                elif len(updated.days) > 1 and searched:
                    # There is no current hotel to keep: the trip has nights and no stay to put in them.
                    # "for the new dates" only when the dates moved: a budget edit keeps them.
                    updated.data_quality.gaps.append(_gap("hotels", _no_hotel_message(date_changed, budget_mode or budget_in_form)))

        if AgentName.WEATHER in agent_names and referred is None:
            weather_result = results.get("weather")
            rows = (weather_result.payload.get("days") if weather_result else []) or []
            weather_by_date = {row.get("date"): row for row in rows}
            for day in updated.days:
                row = weather_by_date.get(day.date.isoformat())
                if row:
                    day.weather = WeatherDay.model_validate(row)

        if shape.requested and referred is None:
            draft.description = shape.reason or apply_plan_shape(updated, shape)
        elif AgentName.ATTRACTION in agent_names:
            attraction_result = results.get("attraction")
            payload = attraction_result.payload if attraction_result else {}
            places = payload.get("places") or []
            meals = payload.get("meals") or []
            if add_requested:
                if places and not _fresh_places(updated, places, message) and AgentName.ATTRACTION in needed:
                    # Every candidate the lookup returned is already in the plan: the source is asked again, for as
                    # many as a trip may ask for, by the same agent through the same toolbox and its cap.
                    places = await self._more_places(
                        context=context,
                        destination=destination,
                        date_range=date_range,
                        toolbox=toolbox,
                        emit=emit,
                        message=message,
                    ) or places
                before_add = {item.id for _, item in updated.iter_items()}
                added = _add_attractions(updated, places, message, day=day_scope)
                if added and more_stops:
                    draft.description = _added_sentence(updated, before_add)
                if not added and _lookup_limit(toolbox, places):
                    updated.data_quality.gaps.append(_limit_gap("attractions"))
                elif not added:
                    wanted = [
                        word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())
                    ]
                    if day_scope is not None:
                        # Said of the day the traveller meant, and true whichever of the two stopped it.
                        kind = wanted[0] if wanted else "stop"
                        outcome = (
                            f"I could not find another verified {kind} for Day {day_scope} that is not already in the "
                            f"plan, or Day {day_scope} had no free time slot; the itinerary stayed as it was."
                        )
                    else:
                        detail = f"a verified {wanted[0]}" if wanted else "a verified stop"
                        outcome = (
                            f"I could not find {detail} from the configured sources, or the day had no free time "
                            "slot; the itinerary stayed as it was."
                        )
                    updated.data_quality.gaps.append(_gap("attractions", outcome))
            elif remove_requested:
                removed = _remove_attraction(updated, draft, message)
                if not removed:
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            "No unlocked stop matched that removal, so the itinerary stayed as it was.",
                        )
                    )
            else:
                resolved = _resolve_target(updated, draft, message)
                if resolved is None:
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            "I could not tell which stop you meant. Name it or give its number in the day"
                            f"{_stop_example(updated)}, or select it first; the itinerary stayed as it was.",
                        )
                    )
                else:
                    day, target_item = resolved
                    before = target_item.name
                    existing = _requested_alternative(updated, message)
                    if existing is not None and existing[1].id != target_item.id:
                        # "change Ocean Park to Disneyland" when Disneyland is
                        # already on another slot: the plan may not hold a place
                        # twice, so explain instead of "no changes needed".
                        day_index = updated.days.index(existing[0]) + 1
                        wanted = existing[1]
                        updated.data_quality.gaps.append(
                            _gap(
                                "attractions",
                                f"{wanted.name} is already in the plan on Day {day_index} at "
                                f"{wanted.start}, so it cannot be added twice. Name a different "
                                f"replacement, or ask me to remove the existing one first.",
                            )
                        )
                    else:
                        wanted_text = _requested_alternative_text(message)
                        cuisine = _cuisine(message)
                        replaced = _replace_target(
                            updated,
                            places,
                            meals,
                            (day, target_item),
                            message,
                            distance_aware=_FAR_MOTION.search(message) is not None,
                            prefer_name=wanted_text,
                            cuisine=cuisine,
                        )
                        closure = _CLOSURE.search(message) is not None
                        # The search the cap refused found nothing because it was never made.
                        limited = not replaced and _lookup_limit(toolbox, meals if cuisine else places)
                        if closure:
                            # A reported closure is a disruption on the plan's record,
                            # whether or not a replacement was found.
                            updated.disruptions.append(
                                Disruption(
                                    kind="closure",
                                    summary=f"{before} was reported closed.",
                                    affected_item_ids=[target_item.id],
                                    resolution=(
                                        f"Replaced with {target_item.name}." if replaced
                                        else f"{TOOL_LIMIT_MESSAGE} The stop is still in the plan." if limited
                                        else "No verified alternative came back; the stop is still in the plan."
                                    ),
                                    resolved=replaced,
                                )
                            )
                        if replaced:
                            draft.description = f"Replaced {before} with {target_item.name}."
                        elif limited:
                            updated.data_quality.gaps.append(_limit_gap("attractions"))
                        else:
                            if cuisine:
                                outcome = f"No verified {cuisine} restaurant came back, so {before} stayed as it was."
                            else:
                                detail = (
                                    f"I could not find a verified {wanted_text!r} in the configured sources"
                                    if wanted_text
                                    else f"No verified alternative for {before!r} came back from the configured sources"
                                )
                                outcome = f"{detail}, so that stop stayed as it was."
                            updated.data_quality.gaps.append(_gap("attractions", outcome))

        if AgentName.TICKET in agent_names and referred is None:
            ticket_result = results.get("ticket")
            flights = (ticket_result.payload.get("flights") if ticket_result else []) or []
            if flights:
                built = self.planner._build_tickets(flights)
                if budget_mode:
                    built = _cheapest_first(built, updated.tickets)
                if built:
                    updated.tickets = built
            if date_changed and old_plan.tickets and not updated.tickets:
                updated.data_quality.gaps.append(_gap("tickets", FLIGHT_NO_LONGER_FITS))
            elif form_changes and old_plan.tickets and not flights:
                updated.data_quality.gaps.append(_gap("tickets", FARE_KEPT))

        if form_changes:
            # What the traveller put in the trip form is what the plan is for now.
            if context.party_size:
                updated.party_size = context.party_size
            if context.origin.strip():
                updated.origin = context.origin.strip()
            draft.description = _details_sentence(context, form_changes)

        if referred:
            keep_only(old_plan, updated, referred)
            draft.description = _one_item_summary(old_plan, updated, referred)
        if _first_flight(old_plan) != _first_flight(updated):
            # The flight's reservation is the plan's first flight: one an edit replaced or dropped is not kept.
            updated.reservations = [
                row for row in updated.reservations if not row.name.startswith("Flight · ")
            ] + _reservations([], updated.tickets)
        # A section this edit fetched again is no longer marked outdated (the next reopen would only clear it).
        still_outdated = outdated_sections(updated, self.settings, utcnow())
        updated.data_quality.freshness = [
            mark
            for mark in updated.data_quality.freshness
            if not (mark.status == "outdated" and mark.section not in still_outdated)
        ]
        # Budget and cost are always recalculated after a change.
        if context.budget is not None:
            updated.budget = Money(amount=context.budget, currency=context.currency)
        updated.cost = self.planner._cost(
            context,
            updated.hotels[0] if updated.hotels else None,
            updated.tickets,
            updated.dates,
        )
        # A budget gap is a statement about this cost against the budget the trip has now: the one an earlier version
        # wrote (for another budget or another cost) goes, and it is written again only if the plan is still over.
        updated.data_quality.gaps = [gap for gap in updated.data_quality.gaps if gap.section != "budget"]
        if updated.cost.status == "over":
            updated.data_quality.gaps.append(_gap("budget", _over_budget_message(updated, cheapest=budget_mode)))
        rule_texts = [*context.hard_constraints, *context.preferences]
        plan_dates = [day.date for day in updated.days]
        updated.constraints = _constraints(
            context,
            updated.days,
            updated.hotels[0] if updated.hotels else None,
            updated.tickets,
            updated.cost,
            blocked=blocked_dates(rule_texts, plan_dates),
            windows=travel_windows(rule_texts, plan_dates),
        )
        updated.updated_at = utcnow()
        updated.change_summary = draft.description or "Applied a local change."
        updated.data_quality.notes = _merge_notes(updated.data_quality.notes, draft.description)
        # A changed day gets transfers that match its new neighbours, from a logged
        # routes.segments call, and no stop starts before its transfer is done
        # (Proposal TC-07 and TC-09).
        await refresh_transfers(
            old_plan,
            updated,
            toolbox=toolbox,
            source=getattr(self.plan_path.registry, "transfers", None),
            buffer=PACE_BUFFER_MINUTES.get(_pace(context), 5),
        )

        errors = validate_plan(updated, self.settings)
        if errors:
            emit("progress", {"phase": "repair", "message": "Repairing the edited itinerary.", "progress": 85})
            updated = await repair_plan(updated, errors, self.gateway, self.settings)
        updated = tidy_data_gaps(updated)

        diff = diff_plans(old_plan, updated)
        if diff:
            updated.version = plan.version + 1
        else:
            # Nothing actually moved. Keeping the version and naming the reason is
            # better than shipping "v2" and telling the traveller nothing changed.
            updated.version = plan.version
            # Only what this turn found is the reason: a gap the plan already had was told when it was written. This
            # turn's are the new ones, what this edit could not do, and the verdicts of the sections it fetched again.
            # The recomputed budget gap is the reason only for a budget request.
            before = {(gap.section, gap.message) for gap in plan.data_quality.gaps}
            news = [
                gap
                for gap in updated.data_quality.gaps
                if (gap.section != "budget" or budget_mode)
                and (
                    (gap.section, gap.message) not in before
                    or is_edit_outcome(gap)
                    or gap.section in refreshed_sections
                )
            ]
            updated.change_summary = news[-1].message if news else NOTHING_MATCHED
        if noted_home is not None:
            # Said where the traveller lives while asking for something else: the home is kept as a note as well.
            _note_home(updated, noted_home)
        # The scope this edit really used, for the account of the decision: one row, one day, or the plan.
        if referred:
            return EditResult(updated, diff, scope="stop", selected=referred == scope_item_id)
        if day_scope is not None and more_stops:
            return EditResult(updated, diff, scope="day", day=day_scope, selected=day_from_selection)
        return EditResult(updated, diff)

    def _grander_stay(self, plan: TripPlan, candidates: list[dict], context: TripContext) -> HotelStay | None:
        """The stay of a higher class than the plan's (a higher rating, else a higher rate) that the budget can still
        pay for, the best rated first; None when no candidate is one. Without a budget, any higher class will do."""
        for row in _grander_candidates(plan, candidates):
            stay = self.planner._build_hotel(row, plan.dates)
            if stay is None:
                continue
            if context.budget is None or self.planner._cost(context, stay, plan.tickets, plan.dates).status != "over":
                return stay
        return None

    async def _more_places(self, *, context, destination, date_range, toolbox: Toolbox, emit, message: str) -> list[dict]:
        """The places of a second lookup with the largest candidate limit a trip may ask for, or [] when the first one
        already asked for that many. The attraction agent runs again, so the toolbox and its per-turn cap apply."""
        if place_candidate_limit(len(date_range.dates)) >= MAX_PLACE_CANDIDATES:
            return []
        results = await self.plan_path.run_agents(
            context=context,
            destination=destination,
            date_range=date_range,
            toolbox=toolbox,
            emit=emit,
            names={AgentName.ATTRACTION},
            extra={**(_search_extra(message) or {}), "places_limit": MAX_PLACE_CANDIDATES},
        )
        result = results.get("attraction")
        return (result.payload.get("places") if result else None) or []

    async def carry_protected(
        self, old: TripPlan, rebuilt: TripPlan, *, context: TripContext, toolbox: Toolbox
    ) -> TripPlan:
        """Put the locked and confirmed stops of ``old`` into a plan built again, with the transfers, the checks and
        the validation that go with them. A plan built again for the same place keeps what the traveller protected."""
        rule_texts = [*context.hard_constraints, *context.preferences]
        plan_dates = [day.date for day in rebuilt.days]
        blocked = blocked_dates(rule_texts, plan_dates)
        before = rebuilt.model_copy(deep=True)
        kept_notes = _carry_protected(old, rebuilt, blocked)
        rebuilt.data_quality.gaps += [_gap("attractions", text) for text in kept_notes]
        if kept_notes:
            # The stops put back belong to their new days: their transfers, and the checks that read the days.
            await refresh_transfers(
                before,
                rebuilt,
                toolbox=toolbox,
                source=getattr(self.plan_path.registry, "transfers", None),
                buffer=PACE_BUFFER_MINUTES.get(_pace(context), 5),
            )
            rebuilt.constraints = _recheck(rebuilt, context, blocked, travel_windows(rule_texts, plan_dates))
            rebuilt.data_quality.gaps += [_gap("transfers", text) for text in _tight_hops(rebuilt)]
        errors = validate_plan(rebuilt, self.settings)
        if errors:
            rebuilt = await repair_plan(rebuilt, errors, self.gateway, self.settings)
        return rebuilt

    async def _extract(
        self,
        message: str,
        plan: TripPlan,
        context: TripContext,
        route_parts: list[str],
        *,
        history: Sequence[Message] = (),
    ) -> ChangeDraft:
        if self.gateway.available:
            # Proposal TC-10: the item ids, days and times an edit can target, and
            # the trip slots, not the stored context or the plan's status fields.
            # The earlier turns say what "add it" or "that hotel" is.
            user = (
                f"Current plan:\n{plan_outline(plan.summary_for_prompt(), ids=True)}\n"
                f"Trip: {trip_slots(context, preferences=True)}\n\n"
                + with_conversation(conversation_block(history, message))
                + fence_user_message(message)
            )
            try:
                data = await self.gateway.json("modify", user=user, purpose="modify.extract")
                draft = ChangeDraft.model_validate(data)
                if not draft.affected_parts:
                    draft.affected_parts = route_parts
                return draft
            except CanaryLeak:
                raise
            except Exception:
                pass
        return ChangeDraft(
            description=message.strip()[:180],
            affected_parts=route_parts or _rule_parts(message),
            target_day=_rule_day(message),
            confidence=0.5,
        )


def _rule_parts(message: str) -> list[str]:
    lower = message.lower()
    parts: list[str] = []
    for name, pattern in [
        ("hotel", r"\b(hotel|stay|room|hostel|ryokan)\b"),
        ("attraction", r"\b(attraction|museum|temple|shrine|sight|stop|visit)\b"),
        ("weather", r"\b(weather|rain|forecast|typhoon)\b"),
        ("ticket", r"\b(flight|train|ticket|fare)\b"),
        ("budget", r"\b(budget|cheaper|expensive|cost)\b"),
        ("dates", r"\b(date|dates|day|nights|extend|shorten)\b"),
    ]:
        if re.search(pattern, lower):
            parts.append(name)
    if asks_for_new_dates(message):
        parts.append("dates")
    day = _rule_day(message)
    if day:
        parts.append(f"day_{day}")
    return sorted(set(parts))


#: The day number a message names; it lives with the rest of "which day is this about" (plan_focus).
_rule_day = day_number_named


#: wording that asks for a *closer* stop rather than a like-for-like swap
_FAR_MOTION = re.compile(
    r"\b(too far|far away|farther|further away|closer|nearer|near by|nearby|walking distance)\b",
    re.IGNORECASE,
)

#: "change A to B" / "swap A for B" — B is what the traveller wants instead
_ALTERNATIVE_TEXT = re.compile(
    r"\b(?:to|into|with|for)\s+([a-z0-9][a-z0-9 &'\-]{2,60})\s*[.?!]*$",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    """Lowercase and drop everything that is not a letter or digit.

    Lets "oceanpark" resolve to "Ocean Park" and "HK Disneyland" to
    "Hong Kong Disneyland", which people type all the time.
    """
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _requested_alternative_text(message: str) -> str | None:
    """The place the traveller asked for, taken from the last "to/into/with/for".

    "i want to change oceanpark ... to disneyland" must yield "disneyland", not
    everything after the first "to".
    """
    cleaned = message.strip().rstrip(" .?!")
    lowered = cleaned.lower()
    best: tuple[int, int] | None = None
    for marker in (" to ", " into ", " with ", " for "):
        index = lowered.rfind(marker)
        if index != -1 and (best is None or index > best[0]):
            best = (index, len(marker))
    if best is None:
        return None
    phrase = cleaned[best[0] + best[1] :].strip(" .,?!")
    if not phrase:
        return None
    # "something quieter", "anything else" are preferences, not place names, and "closer to the hotel" is a distance.
    if phrase.lower().startswith(
        ("some", "any", "else", "other", "another", "different", "cheap", "better", "nice", "quiet", "the hotel")
    ):
        return None
    return phrase


def _requested_alternative(plan: TripPlan, message: str) -> tuple[DayPlan, PlanItem] | None:
    """The stop the traveller asked to swap *in*, if it is already in the plan."""
    phrase = _requested_alternative_text(message)
    if not phrase:
        return None
    needle = _norm(phrase)
    if not needle:
        return None
    best: tuple[DayPlan, PlanItem] | None = None
    for day, item in _items_in_order(plan):
        name = _norm(item.name)
        if name and (name in needle or needle in name):
            if best is None or len(name) > len(_norm(best[1].name)):
                best = (day, item)
    return best


def _has_prices(plan: TripPlan) -> bool:
    """True when at least one line of the plan carries a real price."""
    if plan.cost is not None and plan.cost.lines:
        return True
    if plan.hotels and plan.hotels[0].price_per_night is not None:
        return True
    return any(ticket.price is not None for ticket in plan.tickets)


def _scoped_name(plan: TripPlan, scope_item_id: str | None) -> str:
    if not scope_item_id:
        return ""
    found = plan.find_item(scope_item_id)
    return found[1].name if found else ""


def _items_in_order(plan: TripPlan):
    for day in plan.days:
        for item in day.items:
            yield day, item


def _stop_example(plan: TripPlan) -> str:
    """' (for example "2. <a real stop>")' from the current plan, or '' when it has no stops."""
    for day in plan.days:
        for index, item in enumerate(day.items, start=1):
            if item.name.strip():
                return f' (for example "{index}. {item.name.strip()}")'
    return ""


def _resolve_target(plan: TripPlan, draft: ChangeDraft, message: str) -> tuple[DayPlan, PlanItem] | None:
    """Find the stop the traveller pointed at.

    Accepts, in order: the console's selected item, a leading index ("3." / "#3" /
    "item 3"), a quoted name, a name mentioned anywhere in the sentence, or a
    fuzzy token match. Before this, only a quoted name or "swap X for Y" resolved,
    so "3. Ho Lee Fook too far away" pointed at nothing.
    """
    if draft.target_item_id:
        found = plan.find_item(draft.target_item_id)
        if found:
            return found[0], found[1]

    lowered = message.lower()
    match = re.match(r"\s*#?(\d{1,2})\s*[.)\]:\-\s]", lowered) or re.search(
        r"\b(?:item|stop|number|no\.?)\s*#?(\d{1,2})\b", lowered
    )
    if match:
        index = int(match.group(1))
        for day_index, day in enumerate(plan.days, start=1):
            if draft.target_day and draft.target_day != day_index:
                continue
            if 1 <= index <= len(day.items):
                return day, day.items[index - 1]

    quoted = re.search(r"[\"“']([^\"”']{3,60})[\"”']", message)
    if quoted:
        needle = quoted.group(1).lower()
        for day, item in _items_in_order(plan):
            if needle in item.name.lower():
                return day, item

    # the longest plan item whose name appears verbatim in the sentence
    best: tuple[DayPlan, PlanItem] | None = None
    for day, item in _items_in_order(plan):
        name = item.name.lower()
        if name and name in lowered:
            if best is None or len(name) > len(best[1].name):
                best = (day, item)
    if best is not None:
        return best

    # ignore spacing and punctuation: "oceanpark" must resolve to "Ocean Park"
    squashed = _norm(message)
    squashed_best: tuple[DayPlan, PlanItem] | None = None
    for day, item in _items_in_order(plan):
        name = _norm(item.name)
        if name and name in squashed:
            if squashed_best is None or len(name) > len(_norm(squashed_best[1].name)):
                squashed_best = (day, item)
    if squashed_best is not None:
        return squashed_best

    # token overlap, so a truncated or re-ordered name still resolves: either two
    # shared words, or one distinctive word of four or more characters
    words = {w for w in re.findall(r"[a-z0-9]{3,}", lowered)}
    scored: list[tuple[int, DayPlan, PlanItem]] = []
    for day, item in _items_in_order(plan):
        item_words = {w for w in re.findall(r"[a-z0-9]{3,}", item.name.lower())}
        shared = words & item_words
        if len(shared) >= 2 or any(len(word) >= 4 for word in shared):
            scored.append((len(shared), day, item))
    if scored:
        scored.sort(key=lambda row: -row[0])
        return scored[0][1], scored[0][2]
    return None


def _anchor_geo(plan: TripPlan, day: DayPlan, target: PlanItem):
    """Where "too far away" is measured from: the hotel, else the previous stop."""
    if plan.hotels and plan.hotels[0].geo:
        return plan.hotels[0].geo
    previous = None
    for item in day.items:
        if item is target:
            break
        if item.geo:
            previous = item.geo
    if previous is not None:
        return previous
    for item in day.items:
        if item is not target and item.geo:
            return item.geo
    return plan.destination.geo


def _candidate_pool(item: PlanItem, places: list[dict], meals: list[dict], used: set[str]) -> list[dict]:
    """Meals are replaced from the meal pool, everything else from the place pool."""
    source = meals if item.type is ItemType.MEAL else places
    return [row for row in source if row.get("name") and row["name"] not in used]


_CLOSURE = re.compile(r"\b(closed|closure|shut|suspended|not operating|cancel+ed)\b", re.IGNORECASE)
_ADD_REQUEST = re.compile(r"\b(add|include|insert|append|one more|another|also)\b")
_REMOVE_REQUEST = re.compile(r"\b(remove|delete|drop|take out|without)\b")


def _stated_hours(row: dict, day_date: date | None) -> tuple[list[str], str]:
    """The new place's own hours lines for the day, or "unknown" when its source gave none."""
    stated = [str(line) for line in (row.get("opening_hours") or []) if str(line).strip()]
    lines = lines_for(stated, day_date) if day_date else stated[:7]
    return lines, ("stated" if lines else "unknown")


def _apply_candidate(item: PlanItem, row: dict, day_date: date | None = None) -> None:
    item.opening_hours, item.hours_status = _stated_hours(row, day_date)  # type: ignore[assignment]
    item.name = row["name"]
    # Assignment is not validated, so a raw dict would stay a dict, not a GeoPoint.
    item.geo = GeoPoint.model_validate(row["geo"]) if row.get("geo") else item.geo
    item.address = row.get("address", "")
    item.notes = (row.get("description") or "")[:700]
    refs = row.get("source_refs") or []
    if refs:
        item.source_refs = [SourceRef.model_validate(ref) for ref in refs]
    item.data_status = DataStatus.OK
    item.fetched_at = utcnow()
    item.tags = (row.get("tags") or [])[:4]


def _replace_target(
    plan: TripPlan,
    places: list[dict],
    meals: list[dict],
    target: tuple[DayPlan, PlanItem],
    message: str,
    *,
    distance_aware: bool,
    prefer_name: str | None = None,
    cuisine: str | None = None,
) -> bool:
    day, item = target
    if item.locked or item.confirmed:
        return False
    used = {other.name for _, other in _items_in_order(plan)}
    pool = _candidate_pool(item, places, meals, used)
    if prefer_name:
        needle = _norm(prefer_name)
        preferred = [
            row
            for row in pool
            if needle and (needle in _norm(row["name"]) or _norm(row["name"]) in needle)
        ]
        if preferred:
            pool = preferred
    if not pool:
        return False
    anchor_geo = item.geo or plan.destination.geo
    if anchor_geo is not None:
        local = [row for row in pool if _row_near(anchor_geo, row, 30)]
        if not local:
            return False
        pool = local
    if cuisine:
        hints = _CUISINE_HINTS.get(cuisine, (cuisine,))
        matched = [
            row
            for row in pool
            if any(hint in f"{row.get('name', '')} {row.get('description', '')}".lower() for hint in hints)
        ]
        if not matched:
            return False
        pool = matched
    if distance_aware:
        anchor = _anchor_geo(plan, day, item)
        if anchor is not None:

            def distance(row: dict) -> float:
                geo = row.get("geo") or {}
                lat, lng = geo.get("lat"), geo.get("lng")
                if lat is None or lng is None:
                    return float("inf")
                return haversine_km(anchor.lat, anchor.lng, float(lat), float(lng))

            pool.sort(key=distance)
    _apply_candidate(item, pool[0], day.date)
    return True


_CUISINE_HINTS = {
    "japanese": ("japanese", "sushi", "ramen", "izakaya", "yakitori", "tempura", "udon"),
    "sushi": ("sushi", "japanese"),
    "ramen": ("ramen", "japanese"),
    "chinese": ("chinese", "cantonese", "dim sum", "sichuan"),
    "korean": ("korean", "barbecue"),
    "thai": ("thai",),
    "italian": ("italian", "pizza", "pasta"),
    "indian": ("indian", "curry"),
}


def _cuisine(message: str) -> str | None:
    # The cuisine slot; a language phrase ("in Traditional Chinese") never fills it.
    return cuisine_slot(message)


def _search_extra(message: str) -> dict[str, str] | None:
    extra: dict[str, str] = {}
    cuisine = _cuisine(message)
    if cuisine:
        extra["meals_query"] = f"{cuisine} restaurant"
    places = _places_query(message)
    if places:
        extra["places_query"] = places
    return extra or None


def _places_query(message: str) -> str | None:
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    return " ".join(f"{word}s" for word in wanted) if wanted else None


_MEAL_WORDS = re.compile(
    r"\b(eat|eating|food|meal|dinner|lunch|breakfast|restaurant|cafe|café)\b",
    re.IGNORECASE,
)
_HOTEL_WORDS = re.compile(
    r"\b(hotel|hostel|stay|accommodation|room|ryokan)\b",
    re.IGNORECASE,
)
_CHANGE_WORDS = re.compile(
    r"\b(switch|swap|replace|change|another|different|instead)\b",
    re.IGNORECASE,
)


@dataclass
class PlanAdjust:
    """A pace change. Numbers outside 1–4 places a day are refused."""

    requested: bool = False
    places_per_day: int | None = None
    meals: str | None = None
    earliest: str | None = None
    min_hours: float | None = None
    reason: str = ""


_COUNT_WORDS = {
    "zero": 0, "none": 0, "no": 0,
    "one": 1, "single": 1, "a": 1, "an": 1,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}
_PLACE_WORD = r"(?:place|stop|sight|attraction|visit)"
_MEAL_WORD = r"(?:meal|lunch|dinner|breakfast|dining|restaurant|eat)"
_MAX_PLACES_A_DAY = 4


def plan_shape(message: str) -> bool:
    """True when the sentence asks to reshape the plan rather than one stop."""
    return interpret_plan(message).requested


def interpret_plan(message: str, hinted: dict | None = None) -> PlanAdjust:
    """Read a pace or meal rule from any wording, then refuse the extremes.

    The model may also pass places_per_day and meals in new_values. Those are
    clamped the same way. Hotel, flights and named stops are not touched here.
    """
    lower = message.lower()
    found = PlanAdjust()
    if re.search(r"\b(no places|no stops|no sights|empty itinerary|delete the trip|erase the trip)\b", lower):
        return PlanAdjust(
            requested=True,
            reason="That would remove the trip itself. I can thin a day, not erase it, so nothing was changed.",
        )
    count = re.search(
        rf"\b(\d+|zero|none|no|one|single|two|three|four|five|six)\b.{{0,30}}\b{_PLACE_WORD}s?\b",
        lower,
    )
    about_days = bool(re.search(r"\b(each day|every day|per day|a day)\b", lower))
    if count and (about_days or re.search(r"\b(only|just)\b", lower)):
        raw = count.group(1)
        number = int(raw) if raw.isdigit() else _COUNT_WORDS.get(raw)
        found.requested = True
        if number is None or number < 1 or number > _MAX_PLACES_A_DAY:
            shown = raw if number is None else str(number)
            found.reason = (
                f"I can keep 1 to {_MAX_PLACES_A_DAY} places a day. "
                f"{shown} is outside that, so the plan was not changed."
            )
        else:
            found.places_per_day = number
    if re.search(rf"\b{_MEAL_WORD}", lower):
        drop = bool(re.search(r"\b(no|without|skip|not required|don't need|do not need|drop|remove)\b", lower))
        if drop:
            found.requested = True
            found.meals = "drop"
    hinted = hinted or {}
    if isinstance(hinted.get("places_per_day"), int):
        number = hinted["places_per_day"]
        found.requested = True
        if number < 1 or number > _MAX_PLACES_A_DAY:
            found.places_per_day = None
            found.reason = (
                f"I can keep 1 to {_MAX_PLACES_A_DAY} places a day. "
                f"{number} is outside that, so the plan was not changed."
            )
        elif not found.reason:
            found.places_per_day = number
    if hinted.get("meals") in {"drop", "keep"} and not found.reason:
        found.requested = True
        found.meals = str(hinted["meals"])
    _read_schedule(lower, found)
    if isinstance(hinted.get("min_hours"), (int, float)) and not found.reason:
        hours = float(hinted["min_hours"])
        found.requested = True
        if hours < 1 or hours > 6:
            found.reason = "I can give a place 1 to 6 hours. Longer than that does not fit a day, so nothing was changed."
            found.min_hours = None
        else:
            found.min_hours = hours
    if isinstance(hinted.get("earliest"), str) and re.fullmatch(r"\d{2}:\d{2}", hinted["earliest"]) and not found.reason:
        found.requested = True
        found.earliest = hinted["earliest"]
    if found.earliest and found.min_hours and not found.reason:
        start = _clock_minutes(found.earliest)
        if start + int(found.min_hours * 60) > _DAY_END:
            found.reason = (
                f"A day ends at 21:30, so a {found.min_hours:g}-hour visit cannot start at {found.earliest}. "
                "Nothing was changed."
            )
            found.earliest = None
            found.min_hours = None
    return found


def apply_plan_shape(plan: TripPlan, shape: PlanAdjust) -> str:
    """Apply a pace rule that already passed the bounds check."""
    kept: list[str] = []
    dropped_places: list[str] = []
    dropped_meals: list[str] = []
    for index, day in enumerate(plan.days, start=1):
        if shape.meals == "drop":
            # A confirmed or locked row stays, whatever the pace (Proposal DB-09).
            meals = [item for item in day.items if item.type == ItemType.MEAL and not (item.locked or item.confirmed)]
            dropped_meals.extend(item.name for item in meals)
            meal_ids = {item.id for item in meals}
            day.items = [item for item in day.items if item.id not in meal_ids]
        if shape.places_per_day is not None:
            sights = [item for item in day.items if item.type == ItemType.ATTRACTION]
            if len(sights) > shape.places_per_day:
                protected = [item for item in sights if item.locked or item.confirmed]
                room = max(shape.places_per_day - len(protected), 0)
                free = [item for item in sights if not (item.locked or item.confirmed)]
                keep_ids = {item.id for item in protected} | {item.id for item in free[:room]}
                drop = [item for item in sights if item.id not in keep_ids]
                drop_ids = {item.id for item in drop}
                dropped_places.extend(item.name for item in drop)
                day.items = [item for item in day.items if item.id not in drop_ids]
                sights = [item for item in sights if item.id in keep_ids]
            if sights:
                kept.append(f"Day {index}: " + ", ".join(item.name for item in sights))
    parts: list[str] = []
    if shape.places_per_day is not None:
        parts.append(
            f"Each day now has at most {shape.places_per_day} "
            + ("place" if shape.places_per_day == 1 else "places")
            + (f" ({'; '.join(kept)})" if kept else "")
            + "."
        )
        if dropped_places:
            parts.append("Removed " + ", ".join(dropped_places) + ".")
    if shape.meals == "drop":
        parts.append(
            "Removed meals: " + ", ".join(dropped_meals) + "."
            if dropped_meals
            else "No meals were on the plan."
        )
    if shape.earliest or shape.min_hours:
        moved, dropped = _retime(plan, shape)
        if moved:
            parts.append("Visits now start at " + ", ".join(moved) + ".")
        if dropped:
            parts.append(
                "Could not fit "
                + ", ".join(dropped)
                + " after that start without running past 21:30, so those stops were removed."
            )
        if not moved and not dropped:
            parts.append("No visit needed a new time.")
    if not parts:
        parts.append("The plan already matched that pace.")
    parts.append("Hotel and flights are unchanged.")
    return " ".join(parts)


_DAY_END = 21 * 60 + 30
_MIN_START = 8 * 60
_MAX_START = 18 * 60


def _clock_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _read_schedule(lower: str, found: PlanAdjust) -> None:
    """A start time or a dwell, in whatever form the sentence uses."""
    if found.reason:
        return
    clock = re.search(
        r"\b(?:after|from|past|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        lower,
    )
    compact = re.search(r"\b(?:after|from|past|at)\s+(\d{3,4})\b", lower)
    earliest: int | None = None
    if compact:
        raw = compact.group(1).zfill(4)
        earliest = int(raw[:2]) * 60 + int(raw[2:])
    elif clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        meridiem = clock.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        earliest = hour * 60 + minute
    elif re.search(r"\b(can'?t|cannot|not)\b.{0,24}\b(morning|wake)\b", lower):
        earliest = 11 * 60
    hours = re.search(r"\b(\d+(?:\.\d+)?)\s+hours?\b", lower)
    if hours and re.search(r"\b(spend|least|minimum|stay|at a|single|spot|place)\b", lower):
        found.requested = True
        amount = float(hours.group(1))
        if amount < 1 or amount > 6:
            found.reason = (
                "I can give a place 1 to 6 hours. Longer than that does not fit a day, "
                "so nothing was changed."
            )
            return
        found.min_hours = amount
    if earliest is None:
        return
    found.requested = True
    if earliest < _MIN_START or earliest > _MAX_START:
        found.reason = (
            f"I can start the day between 08:00 and 18:00. {_hhmm(earliest)} is outside that, "
            "so nothing was changed."
        )
        return
    found.earliest = _hhmm(earliest)


def _retime(plan: TripPlan, shape: PlanAdjust) -> tuple[list[str], list[str]]:
    """Move sights to the requested start and length. Flights stay where they are."""
    earliest = _clock_minutes(shape.earliest) if shape.earliest else None
    moved: list[str] = []
    dropped: list[str] = []
    for index, day in enumerate(plan.days, start=1):
        cursor = earliest if earliest is not None else None
        remove: set[str] = set()
        for item in list(day.items):
            if item.type is not ItemType.ATTRACTION:
                continue
            try:
                current = _clock_minutes(item.start)
                end = _clock_minutes(item.end)
            except (ValueError, AttributeError):
                current, end = 9 * 60, 10 * 60 + 30
            duration = max(30, end - current)
            if shape.min_hours:
                duration = max(duration, int(shape.min_hours * 60))
            start = current if cursor is None else max(cursor, earliest or current)
            if start + duration > _DAY_END:
                remove.add(item.id)
                dropped.append(f"Day {index} {item.name}")
                continue
            item.start = _hhmm(start)
            item.end = _hhmm(start + duration)
            moved.append(f"Day {index} {item.name} {_hhmm(start)}–{_hhmm(start + duration)}")
            cursor = start + duration + 20
        if remove:
            day.items = [item for item in day.items if item.id not in remove]
    return moved, dropped


def resolve_referred(plan: TripPlan, message: str, scope_item_id: str | None) -> str | None:
    """The one item this message is about, or None when the edit is section-wide.

    A name in the sentence wins. Otherwise the selected chip ("this") is the item.
    """
    named = _name_mentioned(plan, message)
    if named:
        return named
    if scope_item_id and (plan.find_item(scope_item_id) or plan.find_hotel(scope_item_id)):
        return scope_item_id
    return None


def _name_mentioned(plan: TripPlan, message: str) -> str | None:
    lowered = message.lower()
    found: list[tuple[int, str]] = []
    for _, item in plan.iter_items():
        if len(item.name) >= 4 and item.name.lower() in lowered:
            found.append((len(item.name), item.id))
    for hotel in plan.hotels:
        if len(hotel.name) >= 4 and hotel.name.lower() in lowered:
            found.append((len(hotel.name), hotel.id))
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]


#: a request for more stops, not for one stop: "add more stops", "can u add this days more stop", "one more attraction"
_MORE_STOPS = re.compile(
    r"\bmore\s+(?:\w+\s+){0,2}?(?:stops?|places?|sights?|attractions?|things\s+to\s+(?:do|see))\b"
    r"|\b(?:another|one\s+more|an?\s+extra|extra)\s+(?:\w+\s+)?(?:stops?|places?|sights?|attractions?)\b",
    re.IGNORECASE,
)
#: the day of the stop the page selected: "this day", "that day's", "this days", "the same day"
_THIS_DAY = re.compile(r"\b(?:this|that|the\s+same|same)\s+days?(?:'s)?\b", re.IGNORECASE)
#: wording that makes it something else than an add: a swap, a removal, or a cap ("only two stops")
_NOT_AN_ADD = re.compile(
    r"\b(?:swap|replace|switch|change|instead|remove|delete|drop|take\s+out|without|fewer|less|only)\b", re.IGNORECASE
)
#: a change verb, as the router reads one, and the other words of an edit ("put", "include")
_EDIT_VERB = re.compile(rf"\b{MODIFY_VERBS}\b|\b(?:include|insert|put|delete|get\s+rid\s+of|take\s+out)\b", re.IGNORECASE)
#: the traveller's own home, which no source knows: "the place I live", "where I live", "my flat"
_HOME_WORDS = re.compile(
    r"\b(?:(?:the\s+)?place\s+(?:where\s+)?i\s+(?:live|stay)|where\s+i\s+live|my\s+(?:home|house|flat|apartment))\b",
    re.IGNORECASE,
)
#: an answer that waves the question away: it is not where the traveller lives
_DISMISSAL = re.compile(
    r"^\s*(?:no|nope|nah|no\s+way)\b|\b(?:never\s*mind|forget\s+it|cancel|no\s+thanks|skip\s+it|not\s+now|nothing|"
    r"rather\s+not|don'?t\s+want\s+to\s+say|leave\s+it)\b",
    re.IGNORECASE,
)
#: words that make an answer something other than the name of a place: yes, no, unsure, a first-person verb
_NOT_A_PLACE_WORDS = frozenset(
    (
        # yes, no, a doubt, a sentence about oneself
        "i i'm im i've ive me my mine we we're our us yes yeah yep yup ok okay sure unsure not no nope know don't dont "
        "maybe perhaps idea think guess live living stay staying sorry thanks thank please what why how idk dunno "
        "whatever hm hmm hmmm umm um uh err "
        # a new trip or a change of plan
        "plan planning trip trips go going travel travelling traveling visit instead let's lets about actually "
        "itinerary holiday vacation "
        # an edit, and the words that compare one plan with another
        "make add remove change swap replace switch drop delete keep more less lighter heavier fewer cheaper dearer "
        "grander nicer better busier quieter shorter longer earlier later"
    ).split()
)
#: a short reply to the home question that is no place: a yes, a no, a doubt ("idk", "not sure", "never mind")
_SHORT_REPLY = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|no|nope|nah|ok|okay|sure|idk|dunno|whatever|hm+|um+|maybe|perhaps|not\s+sure|"
    r"i'?m\s+not\s+sure|i\s+don'?t\s+know|don'?t\s+know|no\s+idea|never\s*mind|forget\s+it|cancel|skip(?:\s+it)?|"
    r"no\s+thanks|not\s+now|rather\s+not|leave\s+it)\s*[.!]*\s*$",
    re.IGNORECASE,
)
#: the most words the name of an area or an address is given in
_PLACE_WORDS_MAX = 6
#: the home as the thing to add: "add the place I live", "include my home", "put where I live in the plan"
_ADD_THE_HOME = re.compile(
    r"\b(?:add|include|insert|put)\s+(?:in\s+|back\s+)?(?:\w+\s+){0,1}?"
    r"(?:(?:the\s+)?place\s+(?:where\s+)?i\s+(?:live|stay)|where\s+i\s+live|my\s+(?:home|house|flat|apartment))\b"
    r"|\b(?:add|include|insert|put)\s+(?:it|that)\b",
    re.IGNORECASE,
)
#: "I live in Mong Kok": the home, in the traveller's own words
_I_LIVE_IN = re.compile(
    r"\bi\s+live\s+(?:in|at|near|on)\s+([^\W\d_][\w'’ -]{1,48}?)\s*(?:[,.;!?()]|$|\s+(?:and|but|so|even)\b)",
    re.IGNORECASE,
)


def asks_for_more_stops(message: str) -> bool:
    """The message asks for more stops on a day ("add more stops", "add a museum to this day"), not to change one."""
    lower = message.lower()
    if _NOT_AN_ADD.search(lower):
        return False
    if _MORE_STOPS.search(lower):
        return True
    return _THIS_DAY.search(lower) is not None and _ADD_REQUEST.search(lower) is not None


def _imperative_edit(message: str) -> bool:
    """A command to change the plan ("add it", "can u add this", "change it"), not a wh-question about one."""
    lower = message.lower().strip()
    return bool(_EDIT_VERB.search(lower)) and not re.match(r"^(?:what|where|when|which|who|why|how)\b", lower)


#: a greeting or a filler in front of the sentence proper: "hi, can I ...", "ok can i ...", "sorry, ...", "btw ...",
#: "quick question: ...", "thanks! ...", "hmm ...". "please," with its comma only: "please change the hotel" is a request.
_LEADING_FILLER = re.compile(
    r"^\s*(?:(?:(?:hi|hey|hello)(?:\s+there)?|ok|okay|so|well|um+|hm+|sorry|btw|by\s+the\s+way|quick\s+question"
    r"|just\s+wondering|thanks|thank\s+you)\b[\s,.!:]*|please\s*,\s*)+",
    re.IGNORECASE,
)
#: a change that takes "it", "this" or "that" as its object: with the hotel selected, that is the hotel
_PRONOUN_CHANGE = re.compile(
    r"\b(?:swap|switch|change|replace|move|upgrade|downgrade)\s+(?:it|this|that)\b", re.IGNORECASE
)
#: how a question or a remark opens even when it is typed without "?": "can I change the hotel later", "is the hotel
#: changeable", "should we change the hotel", "I wonder if ...". The "you" forms ("can you", "will you", "is it
#: possible to", "are you able to") and a plain "do change the hotel" are requests.
_QUESTION_OPENER = re.compile(
    r"^\s*(?:"
    r"(?:can|could|may)\s+i|should\s+(?:i|we)|is\s+the|is\s+it\b(?!\s+possible\s+to)|would\s+it"
    r"|will\s+(?:it|the|this|i|we|they)|are\s+(?:there|the|we|they)|does\s+(?:it|the|this)"
    r"|do\s+you\s+think|do\s+(?:i|we|they|you\s+have)"
    r"|i\s+wonder|tell\s+me\s+(?:if|whether)|not\s+sure\s+(?:if|whether)"
    r"|why|when|what|how|where|which"
    r")\b",
    re.IGNORECASE,
)


def _refuses_change(message: str) -> bool:
    """Every change word in the message is refused in its own clause: "don't change the hotel", "I don't want to change
    it", "please don't swap the hotel", "no need to change anything"."""
    found = list(_EDIT_VERB.finditer(message))
    return bool(found) and all(_refused(message, match) for match in found)


def _stay_changes(about: str, *, selected_stay: bool) -> list[re.Match[str]]:
    """The change words aimed at the stay (its name read as "the hotel"): "change the hotel", "a different hotel",
    "the hotel instead", and "swap it" / "change this" when the hotel is the row selected."""
    aimed = [found for found in _AIMED_AT_STAY.finditer(about) if selected_stay or not found.groupdict().get("pronoun")]
    return aimed + list(_CHANGE_HOTEL.finditer(about))


def _wants_another_stay(about: str, *, selected_stay: bool = False) -> bool:
    """A change aimed at the stay that is not refused: "don't move anything, find another hotel" asks for one."""
    return any(not _refused(about, found) for found in _stay_changes(about, selected_stay=selected_stay))


def _stay_change_refused(about: str, *, selected_stay: bool) -> bool:
    """The sentence refuses to change the stay. A refusal counts only when it is aimed at the stay ("don't change the
    hotel, add a cafe on day 2"), and a change of the stay that is not refused wins ("don't remove any stops, I want a
    nicer hotel"). With no change aimed at the stay, every change word refused refuses it too ("no need to change
    anything")."""
    aimed = _stay_changes(about, selected_stay=selected_stay)
    if aimed:
        return all(_refused(about, found) for found in aimed)
    return _refuses_change(about)


def _polite_request(message: str) -> bool:
    """A change request however politely put ("can change the hotel", "could you change this", "help me swap it",
    "pls change hotel", "no need to add anything, just a different hotel please"): no "?", no question opener after a
    greeting, and a change of the stay or a change word that is not refused. "can I change the hotel later", "should we
    change the hotel", "is the hotel changeable", "why this hotel?" and "don't change the hotel" are not requests."""
    if "?" in message or _QUESTION_OPENER.match(_LEADING_FILLER.sub("", message)):
        return False
    if _wants_another_stay(message):
        return True
    return _imperative_edit(message) and not _refuses_change(message)


def _about_another_part(message: str) -> bool:
    """The sentence names a part of the trip other than the hotel, or a day, and no hotel word: "Add a cafe on day 2"
    with the hotel selected is about day 2, not the stay. A budget word ("make it cheaper") still reads with the stay,
    and so does a change of "it" or "this" ("move it closer to the temple")."""
    if _PRONOUN_CHANGE.search(message):
        return False
    parts = set(_rule_parts(message))
    if "hotel" in parts:
        return False
    return bool(parts - {"budget"}) or _rule_day(message) is not None


def _one_question(text: str | None) -> str | None:
    """The writer's question as one line, or None when it gave none."""
    line = " ".join(strip_fence_tags(text or "").split())[:240]
    return line if "?" in line else None


def _missing_fact_question(
    plan: TripPlan,
    message: str,
    draft: ChangeDraft,
    scope_item_id: str | None,
    *,
    anchored: bool,
    home_wanted: bool = False,
) -> tuple[str, str] | None:
    """The one question an edit asks before it changes anything, and the fact it asks for; None when it can act.

    A home the traveller asked to add is asked for (no source knows it). A fact the writer says is missing is asked
    for in the writer's words. An imperative edit the writer could not read (it calls it a question, or gives a low
    confidence) is asked about when nothing on the page or in the message points at a stop, a day or a section."""
    if home_wanted:
        return _one_question(draft.question) or HOME_QUESTION, HOME_FACT
    missing = " ".join(strip_fence_tags(draft.missing_info or "").split())[:120]
    # The writer's own question counts only for a fact it says is missing: an edit it could read is done, not asked.
    if missing:
        fact = HOME_FACT if re.search(r"\b(?:live|lives|home|house|flat|address)\b", missing.lower()) else missing
        return _one_question(draft.question) or f"Could you tell me {missing.rstrip('.?')}?", fact
    if anchored or not _imperative_edit(message):
        return None
    if scope_item_id and (plan.find_item(scope_item_id) or plan.find_hotel(scope_item_id)):
        return None
    if _name_mentioned(plan, message) or _rule_parts(message) or _MEAL_WORDS.search(message):
        return None
    unsure = "confidence" in draft.model_fields_set and draft.confidence < LOW_EDIT_CONFIDENCE
    if draft.is_question or unsure:
        return WHICH_EDIT_QUESTION, ""
    return None


def _stated_home(draft: ChangeDraft, message: str, history: Sequence[Message], *, asked_for_home: bool) -> str | None:
    """Where the traveller lives, as the traveller wrote it, or None.

    Read only when the message is about the home: it asks to add it, says "I live in ...", or answers the question
    for it. The writer may name it from the conversation, but a name is kept only when one of the traveller's own
    turns (not an answer of ours, not a refused turn) holds it: the address is never invented."""
    lived_in = _I_LIVE_IN.search(message)
    if not (asked_for_home or lived_in or _HOME_WORDS.search(message)):
        return None
    candidates: list[str] = []
    written = draft.new_values.get("home")
    if isinstance(written, str):
        candidates.append(written)
    if lived_in:
        candidates.append(lived_in.group(1))
    place = _place_part(message) if asked_for_home else None
    if place:
        candidates.append(place)
    said = [message, *(row.content for row in replayable(history) if row.role == "user")]
    said_folded = [" ".join(strip_fence_tags(line).split()).casefold() for line in said]
    for value in candidates:
        text = " ".join(strip_fence_tags(value).split()).strip(" .,;:!?()")[:80]
        if len(text) >= 2 and any(text.casefold() in line for line in said_folded):
            return text
    return None


def _reads_as_a_place(answer: str) -> bool:
    """A bare answer that can be the name of an area or an address: letters, a few words, and none of the words of a
    yes, a no, a doubt or a sentence about oneself ("I'm not sure", "yes", "ok", "don't know")."""
    text = " ".join(strip_fence_tags(answer).split()).strip(" .!")
    if not text or looks_like_question(text) or _imperative_edit(text) or _DISMISSAL.search(text):
        return False
    words = re.findall(r"[^\W\d_][\w'’.-]*|\d+[\w-]*", text)
    if not words or len(words) > _PLACE_WORDS_MAX or not re.search(r"[^\W\d_]{2}", text):
        return False
    if any(word.casefold().replace("’", "'").strip(".") in _NOT_A_PLACE_WORDS for word in words):
        return False
    # A part of the plan ("the hotel", "day 2", "a museum", "the budget") is not where the traveller lives.
    return not affected_parts_for(text) and _SHORT_REPLY.search(text) is None


def _place_part(answer: str) -> str | None:
    """The place in an answer to the home question: the whole answer when it reads as a place, else its first clause
    when that does and the rest asks for no change ("Mong Kok, we're vegetarian"); None otherwise."""
    text = " ".join(strip_fence_tags(answer).split())
    if _reads_as_a_place(text):
        return text.strip(" .!")
    head, _, rest = text.partition(",")
    if rest and _reads_as_a_place(head) and not (_imperative_edit(rest) or re.search(r"\b(?:plan|trip|go|travel|instead)\b", rest, re.IGNORECASE)):
        return head.strip(" .!")
    return None


def answers_the_home_question(answer: str) -> bool:
    """The message is an answer to "Where do you live?": a place, "I live in X", or a short yes, no or doubt (which is
    asked again or ends the edit). A request of its own ("plan a trip to Osaka", "make day 2 lighter") is not."""
    return _place_part(answer) is not None or _only_says_the_home(answer) or _SHORT_REPLY.search(answer or "") is not None


def _adds_the_home(message: str) -> bool:
    """The home is the object of the add ("add the place I live"), not where something else is ("a cafe near my
    home"). "add it" counts only in a message that also speaks of the home."""
    return _HOME_WORDS.search(message) is not None and _imperative_edit(message) and _ADD_THE_HOME.search(message) is not None


def _only_says_the_home(message: str) -> bool:
    """The message says where the traveller lives and asks for nothing else ("I live in Mong Kok")."""
    return _I_LIVE_IN.search(message) is not None and not _imperative_edit(message)


def _left_as_it_was(plan: TripPlan) -> EditResult:
    unchanged = plan.model_copy(deep=True)
    unchanged.change_summary = LEFT_AS_IT_WAS
    return EditResult(unchanged, [])


def _note_home(plan: TripPlan, home: str) -> None:
    note = f"Home: {home} (as the traveller said; not a verified stop, so it has no time in the plan)."
    notes = [line for line in plan.data_quality.notes if not line.startswith("Home: ")] + [note]
    plan.data_quality.notes = notes[-12:]


def _home_noted(plan: TripPlan, home: str) -> EditResult:
    """The plan with the traveller's home as a note: a home is their own word, not a verified stop, so it gets no
    time slot, no source and no version of its own. The reply says so."""
    noted = plan.model_copy(deep=True)
    _note_home(noted, home)
    noted.change_summary = (
        f"I noted that you live in {home}. A home is not a verified place, so it is kept as a note on the plan "
        "rather than added as a stop; the itinerary is unchanged."
    )
    noted.updated_at = utcnow()
    return EditResult(noted, [])


def _already_planned(plan: TripPlan, row: dict) -> bool:
    """The candidate is a place the plan already has, under this name or under another spelling of it."""
    name = str(row.get("name") or "")
    items = [item for _, item in plan.iter_items()]
    if any(item.name.casefold() == name.casefold() for item in items):
        return True
    try:
        candidate = PlaceCandidate.model_validate({**row, "category": row.get("category") or "attraction"})
    except Exception:
        return False
    return any(
        _same_place(
            PlaceCandidate(
                name=item.name,
                category="restaurant" if item.type is ItemType.MEAL else "attraction",
                geo=item.geo,
                aliases=list(item.names.values()),
            ),
            candidate,
        )
        for item in items
    )


def _fresh_places(plan: TripPlan, places: list[dict], message: str) -> list[dict]:
    """The verified candidates an add may use: not in the plan already, and of the kind the message names."""
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    pool = []
    for row in places:
        if not row.get("name") or not row.get("source_refs") or _already_planned(plan, row):
            continue
        text = f"{row.get('name', '')} {row.get('description', '')}".lower()
        if wanted and not any(word in text for word in wanted):
            continue
        pool.append(row)
    return pool


def _added_sentence(plan: TripPlan, before: set[str]) -> str:
    """"Added X and Y to Day 2.": the stops an add put in, by day."""
    by_day: dict[int, list[str]] = {}
    for index, day in enumerate(plan.days, start=1):
        for item in day.items:
            if item.id not in before:
                by_day.setdefault(index, []).append(item.name)
    parts = [f"{' and '.join(names)} to Day {index}" for index, names in by_day.items()]
    return f"Added {'; '.join(parts)}." if parts else ""


def keep_only(original: TripPlan, updated: TripPlan, item_id: str) -> None:
    """Put back every row except the one the traveller referred to."""
    changed_item = None
    for day in updated.days:
        for item in day.items:
            if item.id == item_id:
                changed_item = item.model_copy(deep=True)
    changed_hotel = next((hotel.model_copy(deep=True) for hotel in updated.hotels if hotel.id == item_id), None)
    if changed_hotel is None and original.find_hotel(item_id) is not None:
        # The hotel search names a stay after the candidate it came from, not after the stay it
        # replaces: the first stay that is not one of the original's takes the referred one's place.
        known = {hotel.id for hotel in original.hotels}
        replacement = next((hotel for hotel in updated.hotels if hotel.id not in known), None)
        if replacement is not None:
            changed_hotel = replacement.model_copy(update={"id": item_id}, deep=True)
    fresh_gaps = [
        gap for gap in updated.data_quality.gaps if gap not in original.data_quality.gaps
    ]

    updated.hotels = [hotel.model_copy(deep=True) for hotel in original.hotels]
    updated.tickets = [ticket.model_copy(deep=True) for ticket in original.tickets]
    updated.dates = original.dates.model_copy(deep=True)
    updated.days = [day.model_copy(deep=True) for day in original.days]
    updated.data_quality = original.data_quality.model_copy(deep=True)

    if changed_item is not None:
        for day in updated.days:
            for index, item in enumerate(day.items):
                if item.id != item_id:
                    continue
                same = changed_item.name == item.name
                day.items[index] = item.model_copy(
                    update={
                        "name": changed_item.name,
                        "geo": changed_item.geo,
                        "address": changed_item.address,
                        "notes": changed_item.notes,
                        "source_refs": changed_item.source_refs,
                        "tags": changed_item.tags,
                        "data_status": changed_item.data_status,
                        "fetched_at": changed_item.fetched_at,
                        "opening_hours": changed_item.opening_hours,
                        "hours_status": changed_item.hours_status,
                    }
                )
                if same and fresh_gaps:
                    updated.data_quality.gaps.append(fresh_gaps[-1])
                return
    if changed_hotel is not None:
        for index, hotel in enumerate(updated.hotels):
            if hotel.id == item_id:
                same = changed_hotel.name == hotel.name
                updated.hotels[index] = changed_hotel
                if same and fresh_gaps:
                    # The stay could not be swapped: why ("No cheaper verified hotel was returned") is the reply,
                    # as it is for a stop that stayed, not "Nothing in the plan matched".
                    updated.data_quality.gaps.append(fresh_gaps[-1])
                return
    if original.find_item(item_id) is not None:
        # The referred stop is gone from the edited plan: it was removed, so it
        # must stay out of the restored days (a replacement keeps its id).
        for day in updated.days:
            day.items = [item for item in day.items if item.id != item_id]
    if fresh_gaps:
        updated.data_quality.gaps.append(fresh_gaps[-1])


def _one_item_summary(original: TripPlan, updated: TripPlan, item_id: str) -> str:
    before = original.find_item(item_id)
    after = updated.find_item(item_id)
    if before and after:
        if before[1].name == after[1].name:
            return f"{before[1].name} stayed as it was. Nothing else was changed."
        return f"Replaced {before[1].name} with {after[1].name}. Everything else is unchanged."
    if before:
        return f"Removed {before[1].name}. Everything else is unchanged."
    old_hotel = original.find_hotel(item_id)
    new_hotel = updated.find_hotel(item_id)
    if old_hotel and new_hotel:
        if old_hotel.name == new_hotel.name:
            return f"{old_hotel.name} stayed as it was. Nothing else was changed."
        return f"Replaced {old_hotel.name} with {new_hotel.name}. Everything else is unchanged."
    return "Nothing else was changed."


def lock_scope(
    agent_names: set[AgentName],
    plan: TripPlan,
    message: str,
    scope_item_id: str | None,
) -> set[AgentName]:
    """A selected meal is a meal change. It must not rewrite the hotel.

    "switch this, I don't want to eat here" was replacing the hotel, including
    once with a stay 11,000 km away. Eating words, or a change aimed at the
    selected stop, stay on that stop unless the message names a hotel.
    """
    names = set(agent_names)
    if _HOTEL_WORDS.search(message):
        return names
    scoped = plan.find_item(scope_item_id) if scope_item_id else None
    about_meal = _MEAL_WORDS.search(message) is not None
    about_change = _CHANGE_WORDS.search(message) is not None
    if about_meal or (scoped is not None and about_change):
        names.discard(AgentName.HOTEL)
        names.discard(AgentName.TICKET)
        names.add(AgentName.ATTRACTION)
    return names


_HOTEL_NOUN = r"(?:hotel|hostel|ryokan|room|accommodation)"
#: "keep the hotel", "keep the same room", "same hotel", "the hotel stays the same": the hotel stays as it is
_KEEP_HOTEL = re.compile(
    r"\b(?:keep|keeping|retain|stick(?:ing)? (?:with|to)|same)\b"
    rf"[^.,;]{{0,16}}?\b{_HOTEL_NOUN}\b|\b{_HOTEL_NOUN}\b\s+(?:(?:stays?|remains?|is|are)\s+)?(?:unchanged|the same)\b",
    re.IGNORECASE,
)
#: the typewriter apostrophe and the curly one a phone types: "don't" and "don’t" are the same word
_NEGATION = re.compile(r"\b(?:not|never|no longer|dont)\b|n['’]t\b", re.IGNORECASE)
#: a negation, or a word that waves a change away ("no need to", "without"), right in front of a change word
_REFUSAL = re.compile(r"\b(?:not|never|no longer|dont|no need|no reason|without)\b|n['’]t\b", re.IGNORECASE)
#: "don't forget to switch the hotel" asks for the change
_DONT_FORGET = re.compile(r"\b(?:don['’]?t|do not|never)\s+(?:forget|hesitate|fail)\s+to\b", re.IGNORECASE)
#: where a clause ends: punctuation, a dash, or a word that starts a new statement ("I don't like this hotel so swap it")
_CLAUSE_END = re.compile(
    r"[,;.:!?–—]|\s-+\s|\b(?:so|therefore|hence|thus|and|but|then|because|since|please|cos|cuz|"
    r"(?:can|could|would|will)\s+you)\b",
    re.IGNORECASE,
)
#: how many words in front of a change word a negation can stand and still refuse it ("no need to", "don't want to")
_REFUSAL_REACH = 3
#: wording that changes the hotel: "swap the hotel", "a different room", "switch to a ryokan"
_CHANGE_HOTEL = re.compile(
    r"\b(?:swap|switch|change|replace|upgrade|downgrade|another|different|other|cheaper|nicer|better|grander"
    r"|luxurious|upscale)\b"
    rf"[^.,;]{{0,20}}?\b{_HOTEL_NOUN}\b|\b{_HOTEL_NOUN}\b[^.,;]{{0,20}}?\b(?P<verb>instead|swap|switch|change)\b",
    re.IGNORECASE,
)


#: a change aimed at the stay itself, read with its name as "the hotel": "swap the hotel", "drop my room", "a different
#: hotel", "the hotel instead", "swap it"
_AIMED_AT_STAY = re.compile(
    rf"\b(?:swap|switch|change|replace|upgrade|downgrade|remove|drop|cancel|delete)\s+(?:\w+\s+){{0,2}}?{_HOTEL_NOUN}\b"
    rf"|\b(?:another|different|other|cheaper|nicer|better|grander|luxurious|upscale)\s+(?:\w+\s+)?{_HOTEL_NOUN}\b"
    rf"|\b{_HOTEL_NOUN}\b\s+(?:instead|swap|switch|change)\b"
    r"|\b(?P<pronoun>(?:swap|switch|change|replace|upgrade|downgrade)\s+(?:it|this|that))\b",
    re.IGNORECASE,
)


def _refused(message: str, found: re.Match[str]) -> bool:
    """The change ``found`` is refused: a negation stands within three words in front of it, in its own clause.

    "don't change the hotel" and "no need to swap it" refuse. A complaint does not: in "I don't like this hotel so
    swap it" the clause of the command begins after "so" (or the comma, the dash, "but"), and a negation farther
    back is about something else."""
    verb = found.start("verb") if found.groupdict().get("verb") else found.start()
    clause = max((cut.end() for cut in _CLAUSE_END.finditer(message, 0, verb)), default=0)
    # "I don't want a hotel swap": the hotel word in front of the change word is part of the change
    ahead = message[clause : found.start() if found.start() >= clause else verb]
    return _REFUSAL.search(" ".join(_DONT_FORGET.sub(" ", ahead).split()[-_REFUSAL_REACH:])) is not None


#: how many words in front of "keep the hotel" a negation can stand and still refuse it ("I don't really want to keep")
_KEEP_REACH = 5


def _keeps_the_hotel(message: str) -> bool:
    """"keep the hotel", "same room" - unless it is refused ("I don't want to keep the hotel").

    The negation has to stand in the keep's own clause: "I don't like the area, but keep the hotel" keeps it."""
    return any(not _negated_keep(message, found) for found in _KEEP_HOTEL.finditer(message))


def _negated_keep(message: str, found: re.Match[str]) -> bool:
    clause = max((cut.end() for cut in _CLAUSE_END.finditer(message, 0, found.start())), default=0)
    ahead = " ".join(_DONT_FORGET.sub(" ", message[clause : found.start()]).split()[-_KEEP_REACH:])
    return _NEGATION.search(ahead) is not None


def _stay_as_word(message: str, name: str) -> str:
    """The message with the stay's name read as the word hotel: "keep Kyoto Garden Inn" is "keep the hotel"."""
    return re.sub(re.escape(name), "the hotel", message, flags=re.IGNORECASE) if name.strip() else message


def _aims_at_the_stay(about: str, *, selected_stop: bool) -> bool:
    """The sentence (the stay's name read as "the hotel") asks to change the stay: a change word aimed at it that is not
    refused, or a keep that is. With a stop selected on the page "it" and "this" are that stop."""
    aimed = any(
        not _refused(about, found) and not (selected_stop and found.groupdict().get("pronoun"))
        for found in _AIMED_AT_STAY.finditer(about)
    )
    return aimed or (_KEEP_HOTEL.search(about) is not None and not _keeps_the_hotel(about))


def _stay_as_a_place(plan: TripPlan, message: str, scope_item_id: str | None) -> HotelStay | None:
    """The stay the sentence names without asking to change it ("add a cafe near Kyoto Garden Inn"), else None.

    A name stands for the stay only when a change is aimed at it. The stay selected on the page is always the one
    meant."""
    named = _name_mentioned(plan, message)
    stay = plan.find_hotel(named) if named else None
    if stay is None or stay.id == scope_item_id:
        return None
    selected_stop = bool(scope_item_id and plan.find_item(scope_item_id))
    return None if _aims_at_the_stay(_stay_as_word(message, stay.name), selected_stop=selected_stop) else stay


def _asks_for_another_hotel(message: str, affected: list[str]) -> bool:
    """The hotel part names a hotel word, which is not yet a wish for another hotel.

    "keep the hotel" is the opposite, and a word like "stay" or "room" in a request for other dates ("extend
    my stay by two days") is about the dates. A message about dates needs wording that changes the hotel.
    """
    if "hotel" not in affected or _keeps_the_hotel(message):
        return False
    # "don't change the hotel", "no need to swap the room": a change word with a refusal in front of it is a keep
    changes = [found for found in _CHANGE_HOTEL.finditer(message) if not _refused(message, found)]
    if asks_for_new_dates(message):
        return bool(changes)
    return bool(changes) or _CHANGE_HOTEL.search(message) is None


def _hours_after_a_move(plan: TripPlan) -> list[str]:
    """What a trip moved to other weekdays can no longer say of its stops' opening hours.

    A stop carries the hours of the weekday it was planned for. A line for another weekday is dropped (the hours are
    unknown now) and the stops are named; a stop whose stored hours say it is closed on its new day is named with it."""
    closed: list[str] = []
    unknown: list[str] = []
    for day in plan.days:
        for item in day.items:
            if item.hours_status != "stated":
                continue
            if windows_on(item.opening_hours, day.date) == []:
                closed.append(f"{item.name} is closed on {day.date:%A} {day.date}, its source says, yet it is in the plan that day.")
            elif only_other_weekdays(item.opening_hours, day.date):
                item.opening_hours, item.hours_status = [], "unknown"
                unknown.append(item.name)
    if unknown:
        closed.append(
            "Opening hours were stated for other days of the week than the new dates; check them for "
            + ", ".join(unknown)
            + "."
        )
    return closed


def _tight_hops(plan: TripPlan) -> list[str]:
    """The stops that start sooner after the stop before them than the travel between the two takes."""
    found: list[str] = []
    for number, day in enumerate(plan.days, start=1):
        ordered = sorted(day.items, key=lambda item: item.start)
        for before, after in zip(ordered, ordered[1:]):
            leg = after.transfer
            if leg is None or leg.from_item_id != before.id:
                continue
            need = leg.minutes + leg.delay_minutes
            if _clock_minutes(after.start) < _clock_minutes(before.end) + need:
                found.append(
                    f"Day {number}: {before.name} ends {before.end} and {after.name} starts {after.start}, but the "
                    f"travel between them takes about {need} min. Locked and confirmed stops keep their times."
                )
    return found


def _first_flight(plan: TripPlan) -> tuple[str, str] | None:
    """The flight the plan's reservation is for: its name and booking link, or None without a flight."""
    return (plan.tickets[0].name, plan.tickets[0].booking_url) if plan.tickets else None


def _form_differences(plan: TripPlan, context: TripContext) -> list[str]:
    """What the trip form says that the plan does not keep: the party, the departure city and the budget (the dates
    are read apart). Hotel style, preferences and the like are not details of the plan."""
    found: list[str] = []
    if context.party_size and context.party_size != plan.party_size:
        found.append("party")
    origin = context.origin.strip()
    if origin and origin.casefold() != (plan.origin or "").strip().casefold():
        found.append("origin")
    if context.budget is not None and (
        plan.budget is None
        or (context.budget, context.currency.upper()) != (plan.budget.amount, plan.budget.currency.upper())
    ):
        found.append("budget")
    return found


def _nothing_differs(plan: TripPlan) -> tuple[TripPlan, list[dict[str, Any]]]:
    """The answer to the page's "generate again" when the form holds nothing the plan does not: the plan, as it was."""
    answer = plan.model_copy(deep=True)
    answer.change_summary = NOTHING_DIFFERS
    return answer, []


def _details_sentence(context: TripContext, changes: list[str]) -> str:
    """The version's summary when the page's "generate again" carried other details than the plan had."""
    parts = []
    if "party" in changes:
        parts.append(f"{context.party_size} traveller{'s' if context.party_size != 1 else ''}")
    if "origin" in changes:
        parts.append(f"departure from {context.origin.strip()}")
    if "budget" in changes:
        parts.append(f"a budget of {context.currency.upper()} {context.budget or 0:,.0f}")
    return "Updated the plan to the trip details: " + ", ".join(parts) + "."


def _hotel_near(plan: TripPlan, row: dict, km: float = 40) -> bool:
    """A stay in another country is not a candidate for this city."""
    return _row_near(plan.destination.geo, row, km)


#: a word in front of a hotel's name that takes it out of the wishes: "not the Hilton", "other than the Hilton"
_NOT_THAT_HOTEL = re.compile(
    r"\b(?:not|no|never|but|than|except|avoid|without|skip|instead of|apart from|aside from)\b|n't\b", re.IGNORECASE
)
#: after a hotel's name: "the Hilton was too dear", "the Hilton isn't great"
_COMPLAINT_AFTER = re.compile(
    r"\s+(?:(?:is|was|are|were)\s+(?:too|not)\b|(?:isn|wasn|aren|weren)'t\b)"
)


def _not_wanted(lowered: str, name: str) -> bool:
    """Every mention of the hotel ``name`` in the lower-cased sentence turns it down or complains of it."""
    mentions = list(re.finditer(re.escape(name), lowered))
    return bool(mentions) and all(
        _NOT_THAT_HOTEL.search(" ".join(re.split(r"[,;.:!?–—]|\s-+\s", lowered[: found.start()])[-1].split()[-6:]))
        or _COMPLAINT_AFTER.match(lowered, found.end())
        for found in mentions
    )


def _swap_choice(plan: TripPlan, candidates: list[dict], *, cheaper: bool, message: str = "") -> dict | None:
    """The stay that replaces the plan's own when the traveller asks for another or a cheaper one, or None.

    Another hotel than the one the plan has, near the city. One the sentence names is the one asked for, unless it names
    it as one it does not want ("not the Hilton", "the Hilton was too dear"); asked for a cheaper stay, it has to be lower
    than the current rate. For "cheaper" the lowest rate first, and only one that is lower than the current rate;
    otherwise the candidate order of the source stands, a stay with a rate before one without (a stay with no rate
    lowers the total by itself)."""
    current = plan.hotels[0].name if plan.hotels else ""
    current_price = plan.hotels[0].price_per_night.amount if plan.hotels and plan.hotels[0].price_per_night else None
    lowered = message.lower()
    pool = [
        row
        for row in candidates
        if row.get("name") != current
        and _hotel_near(plan, row)
        and not (len(row.get("name") or "") >= 4 and _not_wanted(lowered, str(row["name"]).lower()))
    ]
    named = [row for row in pool if len(row.get("name") or "") >= 4 and str(row["name"]).lower() in lowered]
    if current_price is not None and re.search(r"\bcheap", lowered):
        # Asked for a cheaper stay by name: the name is not a way round the rate it has to be lower than.
        named = [row for row in named if (_hotel_price(row) or current_price) < current_price]
    if named:
        return named[0]
    if cheaper:
        pool.sort(key=lambda row: _hotel_price(row) if _hotel_price(row) is not None else float("inf"))
        if current_price is not None:
            pool = [row for row in pool if (_hotel_price(row) or current_price) < current_price]
    else:
        pool.sort(key=lambda row: _hotel_price(row) is None)
    return pool[0] if pool else None


#: a grander stay: "make it more grand", "a grander hotel", "something more luxurious", "upscale". A name such as
#: "Grand Hyatt" is not the word: "grand" alone counts only after "more" or "so".
_GRANDER = re.compile(GRANDER_WORDS, re.IGNORECASE)


def _grander_without_a_stop(message: str) -> bool:
    """Grander wording that names no stop and is a command or a plain class word: "can u make it more grrand",
    "something more luxurious please". Not a bare "more grand", and not "a grander temple"."""
    if "attraction" in affected_parts_for(message):
        return False
    return _imperative_edit(message) or re.search(r"\b(?:luxur\w*|upscale|fancier|posher|gr+ander|gr+andest)\b", message, re.IGNORECASE) is not None


def asks_for_a_grander_stay(message: str) -> bool:
    """The message asks for a stay of a higher class ("grander", "more luxurious", "upscale"), and does not turn it
    down ("no need for anything grander")."""
    for found in _GRANDER.finditer(message):
        clause = max((cut.end() for cut in _CLAUSE_END.finditer(message, 0, found.start())), default=0)
        ahead = " ".join(_DONT_FORGET.sub(" ", message[clause : found.start()]).split()[-_KEEP_REACH:])
        if _REFUSAL.search(ahead) is None:
            return True
    return False


def _grander_candidates(plan: TripPlan, candidates: list[dict]) -> list[dict]:
    """The candidates of a higher class than the plan's stay, best rated first: a higher rating, or with no rating to
    compare, a higher nightly rate. A stay in another city, or the plan's own, is not one."""
    current = plan.hotels[0] if plan.hotels else None
    rate = current.price_per_night.amount if current and current.price_per_night else None
    rating = current.rating if current else None
    pool = []
    for row in candidates:
        if current is not None and row.get("name") == current.name or not _hotel_near(plan, row):
            continue
        row_rating = row.get("rating")
        row_rate = _hotel_price(row)
        higher_rated = isinstance(row_rating, (int, float)) and (rating is None or row_rating > rating)
        dearer = row_rate is not None and rate is not None and row_rate > rate and not (
            isinstance(row_rating, (int, float)) and rating is not None and row_rating < rating
        )
        if current is None or higher_rated or dearer:
            pool.append(row)
    pool.sort(key=lambda row: (-(row.get("rating") or 0), -(_hotel_price(row) or 0)))
    return pool


def _row_near(anchor, row: dict, km: float) -> bool:
    if anchor is None:
        return True
    geo = row.get("geo") or {}
    lat, lng = geo.get("lat"), geo.get("lng")
    if lat is None or lng is None:
        return True
    return haversine_km(anchor.lat, anchor.lng, float(lat), float(lng)) <= km


def _hotel_price(row: dict) -> float | None:
    price = row.get("nightly_price") or {}
    try:
        return float(price.get("amount"))
    except (TypeError, ValueError):
        return None


_ADD_KEYWORDS = (
    "museum",
    "gallery",
    "temple",
    "shrine",
    "park",
    "garden",
    "market",
    "castle",
    "tower",
    "viewpoint",
    "beach",
    "hike",
)

_SLOTS = (("09:00", "10:30"), ("10:30", "12:00"), ("14:00", "15:30"), ("15:30", "17:00"), ("17:00", "18:30"))


def _cheapest_first(tickets: list[TicketOption], current: list[TicketOption]) -> list[TicketOption]:
    """Budget mode: keep the round-trip fare that costs least."""
    priced = [ticket for ticket in tickets if ticket.price]
    if not priced:
        return current
    cheapest = min(priced, key=lambda ticket: ticket.price.amount)  # type: ignore[union-attr]
    previous = current[0] if current else None
    if (
        previous
        and previous.price
        and cheapest.price
        and cheapest.price.amount > previous.price.amount
    ):
        return current
    return [cheapest] + [ticket for ticket in tickets if ticket.id != cheapest.id][:2]


def _free_slot(day: DayPlan) -> tuple[str, str] | None:
    for start, end in _SLOTS:
        if all(end <= item.start or start >= item.end for item in day.items):
            return start, end
    return None


def _late_available(day: DayPlan) -> bool:
    dinner = next(
        (item for item in day.items if item.type.value == "meal" and item.start >= "18:00"),
        None,
    )
    if dinner is None:
        return False
    start, end = "18:30", "19:30"
    return all(end <= item.start or start >= item.end for item in day.items if item is not dinner)


def _late_slot(day: DayPlan) -> tuple[str, str] | None:
    """Use the early evening by moving dinner, instead of refusing a full day."""
    if not _late_available(day):
        return None
    dinner = next(
        item for item in day.items if item.type.value == "meal" and item.start >= "18:00"
    )
    dinner.start = "19:45"
    dinner.end = "21:00"
    return "18:30", "19:30"


def _add_attractions(plan: TripPlan, places: list[dict], message: str, *, day: int | None = None) -> list[str]:
    """Add verified candidates instead of renaming every existing stop. ``day`` (1-based) is the day they go on, when
    the edit is about one day; else the day the message names, else the day with the fewest stops."""
    pool = _fresh_places(plan, places, message)
    if not pool:
        return []

    count = 2 if re.search(
        r"\b(?:two|2)\s+(?:more\s+)?(?:museums?|attractions?|stops?|places?)\b|\ba couple\b",
        message.lower(),
    ) else 1
    day_number = day or _rule_day(message)
    preferred = plan.days[day_number - 1] if day_number and 1 <= day_number <= len(plan.days) else None
    added: list[str] = []
    for row in pool[:count]:
        day = preferred
        if day is None:
            day = min(
                plan.days,
                key=lambda entry: sum(1 for item in entry.items if item.type.value == "attraction"),
            )
        slot = _free_slot(day) or _late_slot(day)
        if slot is None:
            # A targeted day is full; try another day only when the message did
            # not name a day explicitly.
            if preferred is not None:
                break
            day = next(
                (entry for entry in plan.days if _free_slot(entry) is not None or _late_available(entry)),
                None,
            )
            if day is None:
                break
            slot = _free_slot(day) or _late_slot(day)
        start, end = slot
        item_id = f"item_{row.get('id', len(added))}"
        existing_ids = {item.id for day_plan in plan.days for item in day_plan.items}
        if item_id in existing_ids:
            item_id = f"item_added_{len(added)}_{row.get('id', 'x')}"
        hours, hours_status = _stated_hours(row, day.date)
        item = PlanItem(
            opening_hours=hours,
            hours_status=hours_status,
            id=item_id,
            type="attraction",
            name=row["name"],
            start=start,
            end=end,
            geo=row.get("geo"),
            address=row.get("address", ""),
            notes=(row.get("description") or "Added from a verified source.")[:700],
            source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
            data_status=DataStatus.OK,
            fetched_at=utcnow(),
            tags=(row.get("tags") or [])[:4],
            names=trusted_names(row.get("names")),
        )
        day.items.append(item)
        day.items.sort(key=lambda entry: entry.start)
        added.append(item.name)
    return added


def _remove_attraction(plan: TripPlan, draft: ChangeDraft, message: str) -> str | None:
    resolved = _resolve_target(plan, draft, message)
    if resolved is not None:
        day, item = resolved
        if item.locked or item.confirmed:
            return None
        day.items.remove(item)
        return item.name
    # No specific stop was named: fall back to the keyword the traveller used.
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    for day in plan.days:
        for item in list(day.items):
            # A selected stop can be a meal as well as an attraction; the scope
            # chip is about the row the traveller tapped.
            if item.locked or item.confirmed:
                continue
            if not wanted or not any(word in item.name.lower() for word in wanted):
                continue
            day.items.remove(item)
            return item.name
    return None


def _recheck(plan: TripPlan, context: TripContext, blocked, windows) -> list[ConstraintCheck]:
    """The plan's checks read again from its days. The checks for places the traveller named are kept as they
    were: only the planner knows what each place was found as."""
    fresh = _constraints(
        context,
        plan.days,
        plan.hotels[0] if plan.hotels else None,
        plan.tickets,
        plan.cost,
        blocked=blocked,
        windows=windows,
    )
    texts = {check.text for check in fresh}
    named = [check for check in plan.constraints if check.text.startswith("Include ") and check.text not in texts]
    return fresh + named


def _free_start(taken: list[tuple[int, int]], start: int, duration: int) -> int | None:
    """The first start at or after ``start`` where ``duration`` minutes fit between the ``taken`` spans,
    or None when the day has no room left before it ends."""
    for begin, end in sorted(taken):
        if end <= start:
            continue
        if begin >= start + duration:
            break
        start = end
    return start if start + duration <= LATEST_END else None


def _room_for(taken: list[tuple[int, int]], start: int, duration: int) -> int | None:
    """Where a stop that wants ``start`` goes: there when it is free, else the next free time, else the first."""
    if all(end <= start or begin >= start + duration for begin, end in taken):
        return start
    later = _free_start(taken, start, duration)
    return later if later is not None else _free_start(taken, _MIN_START, duration)


def _same_stop(first: PlanItem, second: PlanItem) -> bool:
    """Two rows for one place, however each source spells it (the rule the agents merge places by)."""
    if (first.type is ItemType.MEAL) != (second.type is ItemType.MEAL):
        return False

    def place(item: PlanItem) -> PlaceCandidate:
        category = "restaurant" if item.type is ItemType.MEAL else "attraction"
        return PlaceCandidate(name=item.name, category=category, geo=item.geo, aliases=list(item.names.values()))

    return _same_place(place(first), place(second))


def _twin(stop: PlanItem, plan: TripPlan, put_back: set[str]) -> tuple[int, PlanItem] | None:
    """The rebuild's row for the same place as ``stop``: the same name first, else the same place under another
    spelling. A row that is itself a stop put back is nobody's twin."""
    rows = [(index, item) for index, day in enumerate(plan.days) for item in day.items if item.id not in put_back]
    same_name = next(((index, item) for index, item in rows if item.name.casefold() == stop.name.casefold()), None)
    return same_name or next(((index, item) for index, item in rows if _same_stop(stop, item)), None)


def _hours_for(stop: PlanItem, day_date: date) -> None:
    """A stop moved to another day keeps hours that were stated for its weekday; a line for another one is dropped."""
    if stop.hours_status == "stated" and only_other_weekdays(stop.opening_hours, day_date):
        stop.opening_hours, stop.hours_status = [], "unknown"


def _settle(day: DayPlan) -> list[str]:
    """Take the overlaps out of a day: locked and confirmed stops stay where they are, the others move later
    to the next free time. Returns the stops that no longer fit before the day ends and are left out."""
    fixed = [item for item in day.items if item.locked or item.confirmed]
    others = [item for item in day.items if not (item.locked or item.confirmed)]
    taken = [(_clock_minutes(item.start), _clock_minutes(item.end)) for item in fixed]
    kept, left_out = list(fixed), []
    for item in sorted(others, key=lambda item: item.start):
        start, end = _clock_minutes(item.start), _clock_minutes(item.end)
        begin = _room_for(taken, start, end - start)
        if begin is None:
            left_out.append(item.name)
            continue
        item.start, item.end = _hhmm(begin), _hhmm(begin + end - start)
        taken.append((begin, begin + end - start))
        kept.append(item)
    day.items = sorted(kept, key=lambda item: item.start)
    return left_out


def _carry_protected(old: TripPlan, new: TripPlan, blocked: Collection[date] = ()) -> list[str]:
    """Put the traveller's locked and confirmed stops back after a rebuild; return what the plan should say.

    Each stop of the old plan comes back as the traveller left it (marks and times), once, on the day it was on, or
    on the last day when the new trip is shorter. A day the traveller kept free, or one with no room, is passed
    over for the nearest day that suits. The rebuild's own row for the same place goes, so no place is planned
    twice. Where two protected stops want one time, the later slides to the next free time; any other stop of the
    day gives way by moving later, or is left out when the day has no time left.
    """
    protected = [
        (index, item)
        for index, day in enumerate(old.days)
        for item in sorted(day.items, key=lambda entry: entry.start)
        if item.locked or item.confirmed
    ]
    if not protected or not new.days:
        return []
    used_ids = {item.id for day in new.days for item in day.items}
    put_back: set[str] = set()
    spans: dict[int, list[tuple[int, int]]] = {index: [] for index in range(len(new.days))}
    open_days = [index for index, day in enumerate(new.days) if day.date not in blocked] or list(spans)
    notes: list[str] = []
    touched: set[int] = set()
    for old_index, kept in protected:
        planned: tuple[int, str, str] | None = None
        twin = _twin(kept, new, put_back)
        if twin is not None:
            twin_index, row = twin
            new.days[twin_index].items.remove(row)
            used_ids.discard(row.id)
            planned = (twin_index, row.start, row.end)
            touched.add(twin_index)
        wanted = min(old_index, len(new.days) - 1)
        start, duration = _clock_minutes(kept.start), _clock_minutes(kept.end) - _clock_minutes(kept.start)
        placed_on, begin = wanted, start
        for index in sorted(open_days, key=lambda entry: (abs(entry - wanted), entry > wanted)):
            room = _room_for(spans[index], start, duration)
            if room is not None:
                placed_on, begin = index, room
                break
        restored = kept.model_copy(deep=True)
        if restored.id in used_ids:
            # The rebuild numbers its candidates again, so this id may now mean another place.
            restored.id = new_id("item")
        used_ids.add(restored.id)
        put_back.add(restored.id)
        # The transfer it had was from a neighbour on another day; the day's transfers are fetched again.
        restored.transfer = None
        restored.notes = with_transfer_note(restored.notes, None, meal=restored.type is ItemType.MEAL)
        restored.start, restored.end = _hhmm(begin), _hhmm(begin + duration)
        _hours_for(restored, new.days[placed_on].date)
        spans[placed_on].append((begin, begin + duration))
        new.days[placed_on].items.append(restored)
        if new.days[placed_on].note == NO_STOPS_NOTE:
            new.days[placed_on].note = ""
        touched.add(placed_on)
        if planned != (placed_on, restored.start, restored.end):
            kind = "locked" if kept.locked else "confirmed"
            notes.append(f"Kept your {kind} stop {kept.name} on day {placed_on + 1}.")
    for index in sorted(touched):
        notes += [
            f"There was no time left on day {index + 1} for {name} around your locked stops, so it was left out."
            for name in _settle(new.days[index])
        ]
    return notes


def _ticket_within(ticket: TicketOption, date_range) -> bool:
    """A flight still suits a trip that moved when it leaves on the trip's new first day: a flight of the old
    dates can fall inside the new range (a day earlier) without being a flight for the new trip."""
    if not ticket.depart_at:
        return True
    return ticket.depart_at.date() == date_range.start


def _no_hotel_message(dates_moved: bool, budget: bool) -> str:
    """The gap of a trip with nights and no stay after an edit: "for the new dates" only when the dates moved."""
    if dates_moved:
        return "No verified hotel was returned for the new dates."
    if budget:
        return "No verified hotel was returned for this budget."
    return "No verified hotel was returned."


def _over_budget_message(plan: TripPlan, *, cheapest: bool) -> str:
    """The budget gap of a plan whose cost is over its budget, in today's numbers. ``cheapest``: the edit looked for
    the cheapest stay and fares, so the total is the cheapest verified combination."""
    cost = plan.cost
    budget = cost.budget or 0.0
    over = cost.total - budget
    biggest = max(cost.lines, key=lambda line: line.amount, default=None)
    detail = f" The largest line is {biggest.label} at {cost.currency} {biggest.amount:,.0f}." if biggest else ""
    lead = "The cheapest verified combination is" if cheapest else "The verified cost is"
    return (
        f"{lead} {cost.currency} {cost.total:,.0f}, which is {over:,.0f} over the {cost.currency} {budget:,.0f} "
        f"budget.{detail} A shorter stay, different dates, or a higher budget is needed."
    )


def _gap(section: str, message: str):
    from app.domain.models import DataGap, FailureKind

    return DataGap(section=section, kind=FailureKind.NO_DATA, message=message, retryable=False)


def _lookup_limit(toolbox: Toolbox, found: list) -> bool:
    """The place search found nothing because the per-turn cap refused it: the request never asked the source."""
    return not found and PLACES_SEARCH in getattr(toolbox, "refused", ())


def _limit_gap(section: str):
    """What an edit says when the cap refused its lookup: the limit, as the planning path says it, and not a source's
    answer."""
    from app.domain.models import DataGap, FailureKind

    return DataGap(section=section, kind=FailureKind.BLOCKED, message=TOOL_LIMIT_MESSAGE, retryable=False)


def _merge_notes(notes: list[str], description: str) -> list[str]:
    if description and description not in notes:
        return (notes + [description])[:12]
    return notes
