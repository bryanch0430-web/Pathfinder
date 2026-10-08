"""Attraction agent: "searches notes, cleans them into place records, and geocodes each place".

The cleaning step is a model call, so its output is grounded in code afterwards: a cleaned name
that does not appear in the fetched notes is dropped (the model may not add places from its own
knowledge). Place metadata (closed dates, indoor/outdoor, rating, price, reservation) comes from
the places tool, and every location from the maps tool.
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from backend.agents.geo import normalise_name, slug
from backend.agents.preplanning.base import (
    AgentRun,
    PreplanningAgent,
    failure_reason,
    oldest_fetch,
    source_of,
)
from backend.agents.prompts import (
    ATTRACTION_CLEAN_INSTRUCTION,
    ATTRACTION_CLEAN_SYSTEM,
    ATTRACTION_TOOLS_SYSTEM,
    Purpose,
    data_message,
    system,
)
from backend.agents.runtime.caller import OutputRejected
from backend.agents.runtime.structured import StructuredOutputError, complete_structured
from backend.schemas.agents import AgentResult, AgentTask, AttractionData
from backend.schemas.common import AgentName, SectionStatus, StrictModel
from backend.schemas.trip import ConstraintKind
from backend.schemas.tools import (
    GeocodePayload,
    GeocodeRequest,
    PlaceRecord,
    PlacesPayload,
    PlacesSearchRequest,
    SearchHit,
    SearchPayload,
    ToolOperation,
    ToolOutcome,
    ToolRequest,
    WebSearchRequest,
)
from backend.schemas.trip_plan import Place, SourceRef


class CleanedPlace(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="attraction", max_length=60)
    indoor: bool | None = None
    note: str = Field(default="", max_length=500)


class CleanedPlaces(StrictModel):
    places: list[CleanedPlace]


def _mentioned(name: str, hits: list[SearchHit]) -> bool:
    key = normalise_name(name)
    return bool(key) and any(
        key in normalise_name(h.title) or key in normalise_name(h.snippet) for h in hits
    )


class AttractionAgent(PreplanningAgent):
    name = AgentName.ATTRACTION
    tools_purpose = Purpose.ATTRACTION_TOOLS
    tools_system = ATTRACTION_TOOLS_SYSTEM
    expected_operations = frozenset({ToolOperation.WEB_SEARCH})

    def canonical_requests(self, task: AgentTask) -> list[ToolRequest]:
        dest = task.context.destination
        if not dest:
            return []
        return [WebSearchRequest(query=f"things to do in {dest}", destination=dest, limit=12)]

    async def _run(self, task: AgentTask, run: AgentRun) -> AgentResult:
        outcomes = await self.propose_and_call(task, run)
        searches = [o for o in outcomes if o.ok and isinstance(o.payload, SearchPayload)]
        if not searches:
            failed = [o for o in outcomes if not o.ok]
            return self.unavailable(failure_reason(failed[0]) if failed else "no notes search made")
        hits = [h for o in searches for h in o.payload.hits if isinstance(o.payload, SearchPayload)]
        notes_source = source_of(searches[0])

        cleaned = await self._clean(task, run, hits)
        cleaned = [c for c in cleaned if _mentioned(c.name, hits)]  # grounding: notes only

        destination = task.context.destination or ""
        details = await self.call(
            run, PlacesSearchRequest(destination=destination, category="attraction", limit=30)
        )
        by_name: dict[str, PlaceRecord] = {}
        if details.ok and isinstance(details.payload, PlacesPayload):
            by_name = {normalise_name(r.name): r for r in details.payload.places}

        geocodes = await asyncio.gather(
            *(self.call(run, GeocodeRequest(query=c.name, city=destination)) for c in cleaned)
        )
        excluded = set(task.exclude_ids)
        avoid = {normalise_name(c) for c in task.preferences.avoided_categories} | {
            normalise_name(h.value)
            for h in task.context.hard_constraints
            if h.kind is ConstraintKind.AVOID
        }
        places: dict[str, Place] = {}
        for item, geo in zip(cleaned, geocodes, strict=True):
            place = self._build_place(item, by_name.get(normalise_name(item.name)), geo, details, notes_source)
            if place.place_id in excluded or place.place_id in places:
                continue
            if normalise_name(place.category) in avoid or normalise_name(place.name) in avoid:
                continue
            places[place.place_id] = place

        ranked = sorted(places.values(), key=lambda p: (-(p.rating or 0.0), p.place_id))
        used = [*searches, details] if details.ok else searches
        return AgentResult(
            agent=self.name,
            status=SectionStatus.OK,
            data=AttractionData(places=ranked),
            fetched_at=oldest_fetch(used),
            reason=None if details.ok else "place details unavailable; notes only",
        )

    async def _clean(self, task: AgentTask, run: AgentRun, hits: list[SearchHit]) -> list[CleanedPlace]:
        messages = [
            system(ATTRACTION_CLEAN_SYSTEM, self.deps.canary),
            data_message(
                ATTRACTION_CLEAN_INSTRUCTION,
                [
                    ("agent_task", task.model_dump_json()),
                    ("search_notes", "\n".join(h.model_dump_json() for h in hits)),
                ],
            ),
        ]
        try:
            result = await complete_structured(
                self.deps.agent_caller,
                purpose=Purpose.ATTRACTION_CLEAN,
                messages=messages,
                schema=CleanedPlaces,
                trace=run.trace,
                path=run.path,
                agent=self.name,
                max_repairs=self.deps.settings.max_model_repairs,
            )
            return result.places
        except (OutputRejected, StructuredOutputError):
            # Deterministic fallback grounded in the notes: keep the agent on its task.
            self.deps.observability.record_event(
                run.trace, "attraction_clean_fallback", {"hits": len(hits)}
            )
            return [CleanedPlace(name=h.title) for h in hits]

    @staticmethod
    def _build_place(
        item: CleanedPlace,
        record: PlaceRecord | None,
        geo: ToolOutcome,
        details: ToolOutcome,
        notes_source: SourceRef,
    ) -> Place:
        geo_ok = geo.ok and isinstance(geo.payload, GeocodePayload)
        location = geo.payload.result.location if geo_ok and isinstance(geo.payload, GeocodePayload) else None
        geocode_source = source_of(geo) if geo_ok else None
        if record is not None:
            return Place(
                place_id=record.place_id,
                name=record.name,
                category=record.category,
                location=location or record.location,
                address=record.address,
                rating=record.rating,
                indoor=record.indoor,
                price=record.price,
                needs_reservation=record.needs_reservation,
                closed_dates=record.closed_dates,
                opening_hours=record.opening_hours,
                source=source_of(details),
                geocode_source=geocode_source,
            )
        return Place(
            place_id=f"web-{slug(item.name)}",
            name=item.name,
            category=item.category or "attraction",
            location=location,
            indoor=item.indoor,
            source=notes_source,
            geocode_source=geocode_source,
        )
