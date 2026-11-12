"""The stay research sub-agent: which of the found stays fits what the traveller asked of the stay.

A wish such as "a room with two beds" is not something the hotel search can filter on. This step
reads the wish, asks the hotel source for each candidate's rooms for the trip's dates, and has the
writer judge every candidate against the wish from those rooms, the stay's amenities and (in a
follow-up round) reference pages a web search returned. The judged candidates are ranked and handed
back to the planner.

The writer only judges. It may name a stay only by an id it was given, and its evidence must be a
room name, an amenity or a page title exactly as a source returned it: anything else is dropped, and
a favourable verdict left without evidence becomes ``unclear``. The step fails open: without a
model, a room source or time, the candidates keep the order the hotel agent gave them. A canary leak
is the one failure that is raised.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from app.application.llm_gateway import LLMGateway
from app.application.toolbox import Toolbox
from app.application.web_lookup import lookup_web_references, web_sources
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentResult,
    DateRange,
    HotelStay,
    SourceRef,
    StayMatch,
    TripContext,
    TripPlan,
)
from app.security import strip_fence_tags_deep

#: Words that make a wish one about the stay or its room (English and Chinese).
_STAY_WISH = re.compile(
    r"(?i)\b(?:beds?|twin|double room|king|queen|single room|bunk|bedrooms?|rooms?|suite|hotel|hostel|"
    r"accommodation|apartment|kitchen(?:ette)?|pool|gym|fitness|spa|breakfast included|wi-?fi|parking|"
    r"balcony|bathtub|accessible|wheelchair|elevator|lift|pets?|crib|cot|non-?smoking|smoking|air.?con\w*|"
    r"laundry|washer|washing machine|onsen|connecting|adjoining|ensuite|en-suite)\b"
    r"|[床房]|酒店|飯店|饭店|旅館|旅馆|住宿|民宿|泳池|停車|停车|浴缸|無障礙|无障碍"
)
_URL = re.compile(r"(?i)https?://|www\.")
_VERDICTS = ("meets", "partly", "unclear", "fails")
_RANK = {"meets": 0, "partly": 1, "unclear": 2, "fails": 3}
#: The verdicts that say a stay is worth choosing for the wish.
FAVOURABLE = frozenset({"meets", "partly"})
_REASON_MAX, _EVIDENCE_MAX, _WISH_MAX, _WISHES_MAX = 240, 4, 200, 6
_ROOMS_SHOWN, _AMENITIES_SHOWN, _PAGES_SHOWN = 20, 30, 4


class _ResearchDraft(BaseModel):
    """The writer's raw reply, lenient on purpose: each row is checked on its own in ``_sanitise``."""

    #: Empty by default: a reply without the list judges nothing and is not worth a repair round.
    stays: list[Any] = []


@dataclass
class StayFacts:
    """What the sources gave about one stay: the only things a judgement may rest on."""

    id: str
    name: str
    hotel_class: int | None = None
    amenities: list[str] = field(default_factory=list)
    rooms: list[str] = field(default_factory=list)
    rooms_checked: bool = False
    #: Reference pages from the web search: ``{"title", "url", "snippet", "source"}``.
    pages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResearchReport:
    wishes: list[str] = field(default_factory=list)
    #: Stay id to its judgement, for every stay the writer judged.
    matches: dict[str, StayMatch] = field(default_factory=dict)
    #: Why nothing was judged ("no stay wish", "no model", ...); empty when the step ran.
    skipped: str = ""

    @property
    def best_id(self) -> str | None:
        """The best favourable stay, or None when no stay is known to fit."""
        ranked = sorted(self.matches.items(), key=lambda pair: _order(pair[1]))
        return ranked[0][0] if ranked and ranked[0][1].verdict in FAVOURABLE else None


def stay_wishes(context: TripContext | None) -> list[str]:
    """The traveller's wishes that concern the stay or its room, in their own words."""
    if context is None:
        return []
    lines = [*context.hard_constraints, *context.preferences]
    wishes: list[str] = []
    for line in lines:
        text = " ".join(str(line).split())[:_WISH_MAX]
        if text and _STAY_WISH.search(text) and text not in wishes:
            wishes.append(text)
    return wishes[:_WISHES_MAX]


def _order(match: StayMatch | None) -> tuple[int, int]:
    if match is None:
        return (_RANK["unclear"], 0)
    return (_RANK[match.verdict], -match.score)


def _key(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _payload(wishes: list[str], party_size: int | None, stays: list[StayFacts]) -> dict:
    return {
        "wishes": wishes,
        "party_size": party_size,
        "stays": [
            {
                "id": stay.id,
                "name": stay.name,
                "hotel_class": stay.hotel_class,
                "amenities": stay.amenities[:_AMENITIES_SHOWN],
                "rooms_checked": stay.rooms_checked,
                "rooms": stay.rooms[:_ROOMS_SHOWN],
                "pages": [
                    {
                        "title": str(page.get("title") or ""),
                        "snippet": str(page.get("snippet") or "")[:300],
                    }
                    for page in stay.pages[:_PAGES_SHOWN]
                ],
            }
            for stay in stays
        ],
    }


def _sanitise(
    raw_rows: list[Any], wishes: list[str], stays: list[StayFacts]
) -> dict[str, StayMatch]:
    """The writer's rows made safe: known ids only, evidence only as a source gave it."""
    known = {stay.id: stay for stay in stays}
    out: dict[str, StayMatch] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        stay = known.get(str(raw.get("id") or ""))
        if stay is None or stay.id in out:
            continue
        verdict = str(raw.get("verdict") or "").strip().lower()
        if verdict not in _VERDICTS:
            verdict = "unclear"
        try:
            score = max(0, min(100, int(float(raw.get("score") or 0))))
        except (TypeError, ValueError):
            score = 0
        pages = {
            _key(page.get("title")): page for page in stay.pages if page.get("title")
        }
        allowed = {_key(text): text for text in [*stay.rooms, *stay.amenities]}
        evidence: list[str] = []
        refs: list[SourceRef] = []
        for item in raw.get("evidence") or []:
            key = _key(item)
            if key in allowed and allowed[key] not in evidence:
                evidence.append(allowed[key])
            elif key in pages and str(pages[key]["title"]) not in evidence:
                page = pages[key]
                evidence.append(str(page["title"]))
                refs.append(
                    SourceRef(
                        title=str(page["title"]),
                        url=str(page.get("url") or ""),
                        source=str(page.get("source") or "web"),
                    )
                )
        evidence = evidence[:_EVIDENCE_MAX]
        if verdict in FAVOURABLE and not evidence:
            # A fit nobody can point to is not a fit: the stay is unclear and ranks as one.
            verdict, score = "unclear", min(score, 50)
        reason = " ".join(str(raw.get("reason") or "").split())
        if _URL.search(reason):
            reason = ""
        out[stay.id] = StayMatch(
            wishes=wishes,
            verdict=verdict,  # type: ignore[arg-type]
            score=score,
            reason=reason[:_REASON_MAX],
            evidence=evidence,
            evidence_refs=refs[:_EVIDENCE_MAX],
            rooms_checked=stay.rooms_checked,
        )
    return out


async def judge_stays(
    *,
    wishes: list[str],
    party_size: int | None,
    stays: list[StayFacts],
    gateway: LLMGateway,
    settings: Settings,
) -> dict[str, StayMatch]:
    """One writer call: each stay judged against the wishes. Empty when the call fails (fail open)."""
    if not wishes or not stays or not gateway.available:
        return {}
    user = json.dumps(
        strip_fence_tags_deep(_payload(wishes, party_size, stays)),
        ensure_ascii=False,
        default=str,
    )
    try:
        data = await asyncio.wait_for(
            gateway.json(
                "research", user=user, schema=_ResearchDraft, purpose="stay.research"
            ),
            settings.research_timeout_seconds,
        )
        draft = _ResearchDraft.model_validate(data)
    except CanaryLeak:
        raise
    except Exception:
        return {}
    return _sanitise(draft.stays, wishes, stays)


def _room_source(registry: Any) -> Any | None:
    for source in getattr(registry, "hotels", None) or []:
        if getattr(source, "available", True) and callable(
            getattr(source, "rooms", None)
        ):
            return source
    return None


async def _fetch_rooms(
    rows: list[dict],
    *,
    source: Any,
    toolbox: Toolbox,
    context: TripContext,
    date_range: DateRange,
    budget: float,
) -> None:
    """Each row's rooms for the trip's dates, written on the row. A stay whose rooms did not come stays unchecked."""

    async def one(row: dict) -> None:
        rooms = await toolbox.call(
            "hotels.rooms",
            source.rooms,
            property_token=str(row.get("property_token") or ""),
            name=str(row.get("name") or ""),
            check_in=date_range.start,
            check_out=date_range.end,
            party_size=context.party_size or 1,
            currency=context.currency or "USD",
        )
        row["room_types"] = [room.model_dump(mode="json") for room in rooms or []]
        row["rooms_checked_for"] = {
            "start": date_range.start.isoformat(),
            "end": date_range.end.isoformat(),
        }

    tasks = [asyncio.create_task(one(row)) for row in rows if row.get("property_token")]
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, CanaryLeak):
            raise outcome


def _row_facts(row: dict) -> StayFacts:
    return StayFacts(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        hotel_class=row.get("hotel_class"),
        amenities=[str(item) for item in row.get("amenities") or []],
        rooms=[
            str(room.get("name") or "")
            for room in row.get("room_types") or []
            if isinstance(room, dict)
        ],
        rooms_checked=bool(row.get("rooms_checked_for")),
    )


async def research_stays(
    *,
    results: dict[str, AgentResult],
    context: TripContext,
    date_range: DateRange,
    registry: Any,
    toolbox: Toolbox,
    gateway: LLMGateway,
    settings: Settings,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> ResearchReport:
    """Judge and rank the hotel agent's candidates against the stay wishes, in place.

    Each judged row gains ``match`` (and ``room_types`` when its rooms were fetched), and the rows
    are put best first. The order among equally judged rows is the hotel agent's own.
    """
    wishes = stay_wishes(context)
    if not wishes:
        return ResearchReport(skipped="no stay wish")
    result = results.get("hotel")
    rows = (
        [row for row in (result.payload.get("hotels") or []) if isinstance(row, dict)]
        if result
        else []
    )
    rows = rows[: settings.research_max_hotels]
    if not rows:
        return ResearchReport(wishes=wishes, skipped="no stay candidates")
    if not gateway.available:
        return ResearchReport(wishes=wishes, skipped="no model")
    if emit is not None:
        emit(
            "progress",
            {
                "phase": "research",
                "message": f"Researching which stays fit: {wishes[0]}",
                "progress": 62,
            },
        )
    source = _room_source(registry)
    if source is not None:
        await _fetch_rooms(
            rows,
            source=source,
            toolbox=toolbox,
            context=context,
            date_range=date_range,
            budget=settings.research_budget_seconds,
        )
    matches = await judge_stays(
        wishes=wishes,
        party_size=context.party_size,
        stays=[_row_facts(row) for row in rows],
        gateway=gateway,
        settings=settings,
    )
    if not matches:
        return ResearchReport(wishes=wishes, skipped="research failed")
    for row in rows:
        match = matches.get(str(row.get("id") or ""))
        if match is not None:
            row["match"] = match.model_dump(mode="json")
    hotels = result.payload["hotels"]
    position = {id(row): index for index, row in enumerate(hotels)}
    hotels.sort(
        key=lambda row: (
            *_order(
                matches.get(str(row.get("id") or "")) if isinstance(row, dict) else None
            ),
            position[id(row)],
        )
    )
    return ResearchReport(wishes=wishes, matches=matches)


def pick_researched(
    report: ResearchReport,
) -> Callable[[list[dict]], dict | None] | None:
    """The planner's stay pick after research: the best stay known to fit, when there is one."""
    best = report.best_id
    if best is None:
        return None

    def pick(rows: list[dict]) -> dict | None:
        return next(
            (row for row in rows if row.get("id") == best), rows[0] if rows else None
        )

    return pick


# --- follow-up: reference pages for the stays still unclear -------------------


def _stay_facts(stay: HotelStay) -> StayFacts:
    return StayFacts(
        id=stay.id,
        name=stay.name,
        hotel_class=stay.hotel_class,
        amenities=list(stay.amenities),
        rooms=[room.name for room in stay.room_types],
        rooms_checked=stay.rooms_checked_for is not None,
    )


async def research_unclear_stays(
    plan: TripPlan,
    *,
    context: TripContext,
    registry: Any,
    toolbox: Toolbox,
    gateway: LLMGateway,
    settings: Settings,
) -> dict[str, StayMatch]:
    """A second look at the plan's stays the first round left unclear, with reference pages.

    Up to ``research_follow_up_hotels`` unclear options (the chosen stay first) each get one web
    search for the first wish; the writer judges them again with the pages. Returns the new
    judgements by stay id; the plan is not changed here.
    """
    wishes = stay_wishes(context)
    if not wishes or not gateway.available or not settings.web_search_enabled:
        return {}
    options = list(plan.hotel_options) or list(plan.hotels)
    unclear = [
        stay
        for stay in options
        if stay.match is None or stay.match.verdict == "unclear"
    ]
    unclear = unclear[: settings.research_follow_up_hotels]
    if not unclear:
        return {}
    deepseek, grok = web_sources(registry, settings, toolbox)
    if deepseek is None and grok is None:
        return {}
    city = plan.destination.name or context.destination

    async def pages_for(stay: HotelStay) -> list[dict[str, Any]]:
        query = f"{stay.name} {city} hotel {wishes[0]}"[:160]
        try:
            found = await lookup_web_references(
                section="hotel",
                city=city,
                query=query,
                deepseek=deepseek,
                grok=grok,
                tracer=getattr(toolbox, "tracer", None),
            )
        except CanaryLeak:
            raise
        except Exception:
            return []
        # Only a page that names the stay can say anything about it.
        name = _key(stay.name)
        return [
            page
            for page in found.references
            if name
            and name in _key(f"{page.get('title', '')} {page.get('snippet', '')}")
        ]

    try:
        found = await asyncio.wait_for(
            asyncio.gather(*(pages_for(stay) for stay in unclear)),
            settings.research_follow_up_seconds,
        )
    except asyncio.TimeoutError:
        return {}
    facts: list[StayFacts] = []
    for stay, pages in zip(unclear, found):
        if pages:
            row = _stay_facts(stay)
            row.pages = pages
            facts.append(row)
    if not facts:
        return {}
    return await judge_stays(
        wishes=wishes,
        party_size=context.party_size,
        stays=facts,
        gateway=gateway,
        settings=settings,
    )


__all__ = [
    "FAVOURABLE",
    "ResearchReport",
    "StayFacts",
    "judge_stays",
    "pick_researched",
    "research_stays",
    "research_unclear_stays",
    "stay_wishes",
]
