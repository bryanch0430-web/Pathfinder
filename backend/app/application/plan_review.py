"""One review of a drafted plan: every row, checked by code.

This runs after the timetable exists. It does not search again and it does not call the writer.
Two faults are repaired here: a meal longer than its slot, and a price the source did not give.
Everything else is written as a plan note. A plan that is still structurally illegal goes on to
``validate_plan`` and ``repair_plan``, which already stop after ``max_repairs``.

``refine_max_rounds`` is the operator's cap for the rule pass. The caller skips this review when
that cap is 0. A reviewer model, when one is named, is asked once about the faults the rules
could not fix. It is not the writer. A review that does not answer in time is dropped, and the
drafted plan is kept.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

from typing import Any

from app.application.planner import _must_see, _required_names
from app.application.visit_length import STATED
from app.domain.models import ItemType, PriceKind, TripContext, TripPlan
from app.infrastructure.http import haversine_km

REVIEW_PREFIX = "Plan review: "
REVIEW_CHANGE = "Plan review change: "
MAX_NOTES = 6

logger = logging.getLogger(__name__)


def _review_line(tracer: Any, line: str) -> None:
    meta = getattr(tracer, "metadata", None)
    if isinstance(meta, dict):
        meta.setdefault("review_lines", []).append(line)


def _review_call(tracer: Any, *, model: str, latency_ms: int, asked: list[str], reasoning: str, output: str) -> None:
    record = getattr(tracer, "record_generation", None)
    if not callable(record):
        return
    brief = "The reviewer may change times and wording. It may not add, drop, or rename a place."
    faults = "\n".join(f"- {line}" for line in asked) or "- none"
    record(
        purpose="Reviewer",
        model=model,
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=latency_ms,
        reasoning=reasoning,
        output_preview=output,
        user_preview=f"{brief}\n\nThe rules could not fix:\n{faults}",
    )


def _time_changes(before: TripPlan, after: TripPlan) -> str:
    earlier = {item.id: item for day in before.days for item in day.items}
    lines: list[str] = []
    for day in after.days:
        for item in day.items:
            old = earlier.get(item.id)
            if old is None or (old.start == item.start and old.end == item.end and old.name == item.name):
                continue
            lines.append(f"{old.name}: {old.start}–{old.end} became {item.start}–{item.end}.")
    if not lines:
        return "The reviewer returned a plan. No stop time changed, so the draft was kept as it was."
    return "The reviewer changed:\n" + "\n".join(lines[:12])

#: How long the planner itself gives each meal. A longer stop is shortened back to this
#: unless the traveller stated the length.
MEAL_MAX_MINUTES = {
    "breakfast": 45,
    "lunch": 60,
    "dinner": 75,
    "snack": 30,
}
#: A day this long, or with this many sights, is a full day and should contain a meal.
FULL_DAY_MINUTES = 5 * 60
FULL_DAY_SIGHTS = 2
#: A stop is in the wrong city when it is at least this far from that day's city and clearly
#: closer to another city of the trip.
WRONG_CITY_KM = 80.0
WRONG_CITY_MARGIN_KM = 40.0

_RETURN = re.compile(r"\b(again|return|twice|both days|every day|each day|go back)\b", re.IGNORECASE)
_ESTIMATED_TICKET = frozenset({"drive", "ground"})


@dataclass(frozen=True)
class ReviewCatalog:
    """Names the searches returned, and which of those rows carried a price."""

    places: frozenset[str] = frozenset()
    meals: frozenset[str] = frozenset()
    hotels: frozenset[str] = frozenset()
    tickets: frozenset[str] = frozenset()
    priced: frozenset[str] = frozenset()
    unpriced: frozenset[str] = frozenset()

    def knows(self, kind: str) -> bool:
        return bool(getattr(self, kind))


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    message: str
    fixed: bool = False


def catalog_from_results(results: dict | None) -> ReviewCatalog:
    """The candidate names still held by the agent results. Empty when there are none."""
    if not results:
        return ReviewCatalog()
    places = _rows(results, "attraction", "places")
    meals = _rows(results, "attraction", "meals")
    hotels = _rows(results, "hotel", "hotels")
    tickets = _rows(results, "ticket", "flights")
    priced: set[str] = set()
    unpriced: set[str] = set()
    names = {
        "places": _names(places),
        "meals": _names(meals),
        "hotels": _names(hotels),
        "tickets": _ticket_names(tickets),
    }
    for row in [*places, *meals, *hotels, *tickets]:
        labels = _ticket_names([row]) if row in tickets else _names([row])
        bucket = priced if _row_priced(row) else unpriced
        bucket.update(labels)
    unpriced -= priced
    return ReviewCatalog(
        places=frozenset(names["places"]),
        meals=frozenset(names["meals"]),
        hotels=frozenset(names["hotels"]),
        tickets=frozenset(names["tickets"]),
        priced=frozenset(priced),
        unpriced=frozenset(unpriced),
    )


def review_plan(
    plan: TripPlan,
    context: TripContext | None = None,
    catalog: ReviewCatalog | None = None,
) -> tuple[TripPlan, list[ReviewFinding]]:
    """A copy of ``plan`` with the fixes applied and the remaining faults noted. ``plan`` is unchanged."""
    reviewed = plan.model_copy(deep=True)
    catalog = catalog or ReviewCatalog()
    findings = _findings(reviewed, context, catalog)
    _write_notes(reviewed, findings)
    return reviewed, findings


def _findings(plan: TripPlan, context: TripContext | None, catalog: ReviewCatalog) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for day in plan.days:
        ordered = sorted(day.items, key=lambda item: item.start)
        for index, item in enumerate(ordered):
            following = ordered[index + 1] if index + 1 < len(ordered) else None
            if item.type is ItemType.ATTRACTION:
                _unknown(findings, item.name, catalog.places | _alternative_names(item), "places", catalog)
            elif item.type is ItemType.MEAL:
                _unknown(findings, item.name, catalog.meals | catalog.places | _alternative_names(item), "meals", catalog)
                findings.extend(_meal_length(item, following))
            if item.type in {ItemType.ATTRACTION, ItemType.MEAL}:
                _unsourced_price(
                    findings, item.name, item.price, item.price_kind, bool(item.source_refs), catalog,
                    clear=lambda item=item: _clear_item_price(plan, item),
                )
        findings.extend(_no_meal(day, ordered))
        findings.extend(_wrong_city(plan, day, ordered))
    findings.extend(_repeated(plan, context))
    findings.extend(_missing(plan, context))
    for hotel in plan.hotels:
        known = catalog.hotels | {_fold(option.name) for option in plan.hotel_options if option.source_refs}
        _unknown(findings, hotel.name, known, "hotels", catalog)
        if not hotel.source_refs:
            findings.append(ReviewFinding("no_source", f"{hotel.name} has no source."))
        _unsourced_price(
            findings, hotel.name, hotel.price_per_night, hotel.price_kind, bool(hotel.source_refs), catalog,
            clear=lambda hotel=hotel: _clear_stay_price(plan, hotel),
        )
    for ticket in plan.tickets:
        if ticket.mode in _ESTIMATED_TICKET:
            continue
        _unknown(findings, ticket.name, catalog.tickets, "tickets", catalog)
        if not ticket.source_refs:
            findings.append(ReviewFinding("no_source", f"{ticket.name} has no source."))
        _unsourced_price(
            findings, ticket.name, ticket.price, ticket.price_kind, bool(ticket.source_refs), catalog,
            clear=lambda ticket=ticket: _clear_ticket_price(plan, ticket),
        )
    return findings


def _unknown(findings: list[ReviewFinding], name: str, known: frozenset[str], kind: str, catalog: ReviewCatalog) -> None:
    if not catalog.knows(kind) or _listed(name, known):
        return
    findings.append(ReviewFinding("unknown_name", f"{name} is not among the {kind} that were found."))


def _meal_length(item, following) -> list[ReviewFinding]:
    if STATED in item.tags or not item.meal_slot:
        return []
    limit = MEAL_MAX_MINUTES.get(item.meal_slot)
    if limit is None:
        return []
    start, end = _minutes(item.start), _minutes(item.end)
    if end - start <= limit:
        return []
    shortened = start + limit
    if following is not None and shortened > _minutes(following.start):
        return [ReviewFinding(
            "meal_long",
            f"{item.name} runs {end - start} min; a {item.meal_slot} stop is {limit} min, and the next stop leaves no room to shorten it.",
        )]
    item.end = _hhmm(shortened)
    return [ReviewFinding(
        "meal_long",
        f"{item.name} ran {end - start} min. A {item.meal_slot} stop is {limit} min, so it now ends at {item.end}.",
        fixed=True,
    )]


def _unsourced_price(findings, name, price, kind, has_source, catalog: ReviewCatalog, *, clear) -> None:
    if price is None or kind is PriceKind.ESTIMATE or kind is PriceKind.NONE:
        return
    known = _price_known(name, catalog)
    if known is True or (known is None and has_source):
        return
    clear()
    findings.append(ReviewFinding(
        "unsourced_price",
        f"The price on {name} was removed: the source did not give one.",
        fixed=True,
    ))


def _no_meal(day, ordered) -> list[ReviewFinding]:
    sights = [item for item in ordered if item.type is ItemType.ATTRACTION]
    if any(item.type is ItemType.MEAL for item in ordered) or not sights:
        return []
    span = _minutes(ordered[-1].end) - _minutes(ordered[0].start)
    if len(sights) < FULL_DAY_SIGHTS and span < FULL_DAY_MINUTES:
        return []
    return [ReviewFinding("no_meal", f"{day.date:%A} has no meal.")]


def _repeated(plan: TripPlan, context: TripContext | None) -> list[ReviewFinding]:
    seen: dict[str, str] = {}
    findings: list[ReviewFinding] = []
    for day in plan.days:
        for item in day.items:
            if item.type is not ItemType.ATTRACTION:
                continue
            folded = _fold(item.name)
            if not folded or _asked_to_return(item.name, context):
                continue
            if folded in seen and seen[folded] != day.date.isoformat():
                findings.append(ReviewFinding("repeat_place", f"{item.name} is on more than one day."))
            seen.setdefault(folded, day.date.isoformat())
    return findings


def _missing(plan: TripPlan, context: TripContext | None) -> list[ReviewFinding]:
    if context is None:
        return []
    present = [_fold(item.name) for day in plan.days for item in day.items]
    findings: list[ReviewFinding] = []
    for name in [*_must_see(context), *_required_names(context)]:
        folded = _fold(name)
        if folded and not any(folded in other or other in folded for other in present if other):
            findings.append(ReviewFinding("missing_place", f"{name} was named for this trip and is not on the plan."))
    return findings


def _wrong_city(plan: TripPlan, day, ordered) -> list[ReviewFinding]:
    if len(plan.segments) < 2:
        return []
    own = plan.segment_of(day.date).destination
    if own.geo is None:
        return []
    others = [segment.destination for segment in plan.segments if segment.destination.geo is not None and segment.destination.name != own.name]
    if not others:
        return []
    findings: list[ReviewFinding] = []
    for item in ordered:
        if item.type is not ItemType.ATTRACTION or item.geo is None:
            continue
        here = haversine_km(item.geo.lat, item.geo.lng, own.geo.lat, own.geo.lng)
        nearest = min(haversine_km(item.geo.lat, item.geo.lng, city.geo.lat, city.geo.lng) for city in others)
        if here >= WRONG_CITY_KM and here >= nearest + WRONG_CITY_MARGIN_KM:
            findings.append(ReviewFinding("wrong_city", f"{item.name} on {day.date:%A} is in another city from that day."))
    return findings


def _asked_to_return(name: str, context: TripContext | None) -> bool:
    if context is None:
        return False
    folded = _fold(name)
    for text in [*context.preferences, *context.hard_constraints]:
        line = _fold(str(text))
        if folded and folded in line and _RETURN.search(str(text)):
            return True
    return False


def _price_known(name: str, catalog: ReviewCatalog) -> bool | None:
    folded = _fold(name)
    if not folded:
        return None
    if any(folded in other or other in folded for other in catalog.priced):
        return True
    if any(folded in other or other in folded for other in catalog.unpriced):
        return False
    return None


def _clear_item_price(plan: TripPlan, item) -> None:
    item.price = None
    item.price_kind = PriceKind.NONE
    _drop_cost(plan, item.name)


def _clear_stay_price(plan: TripPlan, hotel) -> None:
    hotel.price_per_night = None
    hotel.price_kind = PriceKind.NONE
    _drop_cost(plan, hotel.name)


def _clear_ticket_price(plan: TripPlan, ticket) -> None:
    ticket.price = None
    ticket.price_kind = PriceKind.NONE
    _drop_cost(plan, ticket.name)


def _drop_cost(plan: TripPlan, name: str) -> None:
    kept = [line for line in plan.cost.lines if name not in line.label]
    if len(kept) == len(plan.cost.lines):
        return
    plan.cost.lines = kept
    plan.cost.total = round(sum(line.amount for line in kept), 2)
    if not kept:
        plan.cost.remaining = None
        plan.cost.status = "unknown"
        return
    if plan.cost.budget is not None and plan.cost.status in {"within", "over"}:
        plan.cost.remaining = round(plan.cost.budget - plan.cost.total, 2)
        plan.cost.status = "within" if plan.cost.total <= plan.cost.budget else "over"


def _write_notes(plan: TripPlan, findings: list[ReviewFinding]) -> None:
    notes = [note for note in plan.data_quality.notes if not note.startswith(REVIEW_PREFIX) and not note.startswith(REVIEW_CHANGE)]
    changes = [f"{REVIEW_CHANGE}{finding.message}" for finding in findings if finding.fixed]
    notes.extend(changes[:MAX_NOTES])
    written = 0
    for finding in findings:
        if finding.fixed:
            continue
        note = f"{REVIEW_PREFIX}{finding.message}"
        if note in notes or written >= MAX_NOTES:
            continue
        notes.append(note)
        written += 1
    plan.data_quality.notes = notes


def _rows(results: dict, agent: str, field: str) -> list[dict]:
    result = results.get(agent)
    payload = getattr(result, "payload", None) or {}
    return [row for row in (payload.get(field) or []) if isinstance(row, dict)]


def _names(rows: list[dict]) -> set[str]:
    return {_fold(str(row.get("name") or "")) for row in rows if row.get("name")}


def _ticket_names(rows: list[dict]) -> set[str]:
    names: set[str] = set()
    for row in rows:
        for key in ("name", "airline", "flight_number"):
            if row.get(key):
                names.add(_fold(str(row[key])))
        if row.get("airline") and row.get("flight_number"):
            names.add(_fold(f"{row['airline']} {row['flight_number']}"))
    return {name for name in names if name}


def _row_priced(row: dict) -> bool:
    for key in ("price", "nightly_price"):
        value = row.get(key)
        if isinstance(value, dict) and value.get("amount"):
            return True
        if isinstance(value, (int, float)) and value > 0:
            return True
        if isinstance(value, str) and value.strip() not in {"", "0", "0.0"}:
            return True
    return False


def _alternative_names(item) -> frozenset[str]:
    return frozenset(_fold(option.name) for option in item.alternatives if option.name)


def _listed(name: str, known: frozenset[str]) -> bool:
    folded = _fold(name)
    if not folded or not known:
        return not known
    if folded in known:
        return True
    return any(len(folded) >= 4 and (folded in other or other in folded) for other in known)


def _fold(name: str) -> str:
    return " ".join(name.casefold().split())


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def reviewer_llm(settings: Any):
    """The reviewer's own model, or None when the review stays on the rules.

    An arm key wins. With none saved, Poe uses the server Poe key and DeepSeek uses the server DeepSeek key.
    Reasoning is sent only when the operator turned it on.
    """
    model = str(getattr(settings, "reviewer_model", "") or "").strip()
    if not model:
        return None
    from app.infrastructure.anthropic_llm import AnthropicLLM
    from app.infrastructure.llm import OpenAICompatibleLLM
    from app.infrastructure.llm_providers import PROVIDERS, server_deepseek_key
    provider = str(getattr(settings, "reviewer_provider", "") or "poe").strip().lower()
    spec = PROVIDERS.get(provider)
    key = str(getattr(settings, "reviewer_api_key", "") or "").strip()
    if not key and provider == "poe":
        key = str(getattr(settings, "poe_api_key", "") or "").strip()
    if not key and provider == "deepseek":
        key = server_deepseek_key(settings)
    if spec is None or not key:
        return None
    temperature = float(getattr(settings, "reviewer_temperature", 0.0))
    max_tokens = int(getattr(settings, "reviewer_max_tokens", 4096) or 4096)
    reasoning = str(getattr(settings, "reviewer_reasoning", "") or "none").strip().lower()
    if reasoning not in {"none", "low", "medium", "high"}:
        reasoning = "none"
    # A long think does not return a rewrite of the whole plan before the turn is stopped.
    # Only a short effort is sent. Medium and high are left off this call.
    base_url = str(getattr(settings, "reviewer_base_url", "") or "")
    if spec.wire == "anthropic":
        return AnthropicLLM(
            settings, api_key=key, model=model, fixed_temperature=temperature, fixed_max_tokens=max_tokens
        )
    extra = {"reasoning_effort": "low"} if reasoning == "low" else {}
    return OpenAICompatibleLLM(
        settings,
        base_url=spec.endpoint(base_url),
        api_key=key,
        model=model,
        label=spec.label,
        fixed_temperature=temperature,
        fixed_max_tokens=max_tokens,
        extra=extra,
    )


_REVIEW_SYSTEM = (
    "You review a drafted trip plan. You may change times and wording. "
    "Do not add, drop, or rename a place, hotel, or ticket. "
    "Do not add a price, a source URL, or a coordinate. "
    "Return the complete plan JSON only."
)


async def apply_reviewer(
    plan: TripPlan,
    findings: list[ReviewFinding],
    settings: Any,
    llm: Any,
    tracer: Any = None,
) -> TripPlan | None:
    """One call to the reviewer model about the faults the rules could not fix.

    None when the model is absent, the answer is unusable, or it would add or remove a fact.
    The plan passed in is not changed. What was asked, and what became of the answer, is written
    onto ``tracer`` for the decision under the reply.
    """
    from app.application.llm_gateway import _extract_json
    from app.application.validation import _refusal, _restore_facts, validate_plan

    open_findings = [finding for finding in findings if not finding.fixed]
    if llm is None or not open_findings:
        return None
    budget = float(getattr(settings, "reviewer_timeout_seconds", 20.0) or 20.0)
    asked = [finding.message for finding in open_findings]
    model = str(getattr(llm, "name", "") or "")
    started = time.perf_counter()
    user = (
        "The rules could not fix these:\n"
        + "\n".join(f"- {line}" for line in asked)
        + "\n\nPlan JSON:\n"
        + plan.model_dump_json()
    )
    try:
        result = await asyncio.wait_for(
            llm.complete(
                system=_REVIEW_SYSTEM,
                user=user,
                purpose="plan.review",
                json_mode=True,
                temperature=float(getattr(settings, "reviewer_temperature", 0.0)),
                max_tokens=int(getattr(settings, "reviewer_max_tokens", 4096) or 4096),
                timeout=budget,
                retries=0,
            ),
            timeout=budget,
        )
    except TimeoutError:
        logger.warning("plan review timed out after %.0fs; the drafted plan is kept", budget)
        _review_call(
            tracer, model=model, latency_ms=int(budget * 1000), asked=asked, reasoning="",
            output=f"No answer within {budget:g} seconds. The drafted plan was kept.",
        )
        return None
    except Exception:
        _review_call(
            tracer, model=model, latency_ms=int((time.perf_counter() - started) * 1000), asked=asked,
            reasoning="", output="The review failed. The drafted plan was kept.",
        )
        return None
    latency_ms = int(getattr(result, "latency_ms", 0) or (time.perf_counter() - started) * 1000)
    model = str(getattr(result, "model", "") or model)
    reasoning = str(getattr(result, "reasoning", "") or "")
    data = _extract_json(getattr(result, "text", "") or "")
    if data is None:
        _review_call(tracer, model=model, latency_ms=latency_ms, asked=asked, reasoning=reasoning, output="The answer was not a plan. The drafted plan was kept.")
        return None
    try:
        candidate = TripPlan.model_validate(data)
    except Exception:
        _review_call(tracer, model=model, latency_ms=latency_ms, asked=asked, reasoning=reasoning, output="The answer was not a plan. The drafted plan was kept.")
        return None
    candidate.trip_id = plan.trip_id
    candidate.session_id = plan.session_id
    candidate.version = plan.version
    candidate.created_at = plan.created_at
    candidate.updated_at = plan.updated_at
    if _refusal(plan, candidate):
        _review_call(
            tracer, model=model, latency_ms=latency_ms, asked=asked, reasoning=reasoning,
            output="The answer would add, drop, or rename a place, so it was not used. The drafted plan was kept.",
        )
        return None
    _restore_facts(plan, candidate)
    if validate_plan(candidate, settings):
        _review_call(
            tracer, model=model, latency_ms=latency_ms, asked=asked, reasoning=reasoning,
            output="The answer failed the plan checks, so it was not used. The drafted plan was kept.",
        )
        return None
    _review_call(
        tracer, model=model, latency_ms=latency_ms, asked=asked, reasoning=reasoning,
        output=_time_changes(plan, candidate),
    )
    return candidate
