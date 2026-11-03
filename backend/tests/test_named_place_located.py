"""A place that is in the plan is not also reported as left out.

A guide can list a place without coordinates that the geocoder then cannot place or check, while the
place source, asked for it by name because the traveller named it, answers with coordinates. The place
is in the plan, so neither the note "left out ... could not locate" nor the gap "could not be checked,
ask again" may name it.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.application.agents import AgentContext, AttractionAgent
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    AgentResult,
    DateRange,
    FailureKind,
    GeoLocation,
    GeoPoint,
    PlaceCandidate,
    SourceRef,
    TripContext,
)
from app.infrastructure.observability import NoopTracer

KYOTO = GeoPoint(lat=35.0116, lng=135.7681)
REF = SourceRef(title="guide", url="https://example.com/guide", source="guide")
GION = PlaceCandidate(name="Gion Corner", geo=GeoPoint(lat=35.0037, lng=135.7754), source_refs=[REF])


class _Guide:
    """Lists places without coordinates; answers a search by name with `named[query]`, coordinates and all."""

    name = "guide"
    available = True
    ranks_by_relevance = False

    def __init__(self, listing: list[str], named: dict[str, PlaceCandidate]) -> None:
        self.listing = listing
        self.named = named

    async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
        query = kwargs.get("query")
        if query in self.named:
            return [self.named[query].model_copy(deep=True)]
        if kwargs.get("category") != "attraction":
            return []
        return [PlaceCandidate(name=name, source_refs=[REF]) for name in self.listing]


class _Geocoder:
    """Answers each call from `answers` in turn (the last one repeats): a place, None, or an exception."""

    def __init__(self, *answers: Any) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def geocode(self, name: str, **_: Any) -> GeoLocation | None:
        self.asked.append(name)
        answer = self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _near(index: int) -> GeoLocation:
    return GeoLocation(name=f"p{index}", geo=GeoPoint(lat=KYOTO.lat + 0.03 * index, lng=KYOTO.lng), source="fake")


def _run(settings: Settings, guide: _Guide, geocoder: _Geocoder) -> AgentResult:
    registry = SimpleNamespace(places=[guide], notes=[], geocoder=geocoder, web=[], web_deep=[])
    start = date.today() + timedelta(days=30)
    request = AgentContext(
        context=TripContext(
            session_id="s",
            destination="Kyoto",
            start_date=start,
            end_date=start + timedelta(days=2),
            preferences=["Gion Corner"],
        ),
        destination=GeoLocation(name="Kyoto", geo=KYOTO),
        date_range=DateRange(start=start, end=start + timedelta(days=2)),
        request_id="req_named",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_named"),
        settings=settings,
    )
    return asyncio.run(AttractionAgent(registry, settings).run(request))


def _gaps(result: AgentResult) -> list[tuple[FailureKind, bool, str]]:
    return [(gap.kind, gap.retryable, gap.message) for gap in result.failures if gap.section == "attractions"]


def _names(result: AgentResult) -> list[str]:
    return [place["name"] for place in result.payload["places"]]


def test_a_named_place_that_arrives_located_is_not_reported_as_one_the_geocoder_could_not_locate(settings: Settings) -> None:
    guide = _Guide(["Gion Corner", "Nijo Castle"], {"Gion Corner": GION})
    geocoder = _Geocoder(None, _near(1))  # the geocoder knows no "Gion Corner, Kyoto"

    result = _run(settings, guide, geocoder)

    assert _names(result) == ["Gion Corner", "Nijo Castle"] and result.payload["wishes"] == {"Gion Corner": "Gion Corner"}
    assert not any("could not locate" in note or "Gion Corner" in note for note in result.notes), result.notes
    assert _gaps(result) == []


def test_a_named_place_that_arrives_located_is_not_a_gap_to_retry_because_the_geocoder_timed_out(settings: Settings) -> None:
    guide = _Guide(["Gion Corner", "Nijo Castle"], {"Gion Corner": GION})
    geocoder = _Geocoder(asyncio.TimeoutError(), _near(1))

    result = _run(settings, guide, geocoder)

    assert _names(result) == ["Gion Corner", "Nijo Castle"]
    assert _gaps(result) == [], "the place is in the plan, so there is nothing to ask again about"
    assert not any("could not be checked" in note for note in result.notes), result.notes


def test_the_count_of_places_left_out_does_not_include_the_named_one(settings: Settings) -> None:
    guide = _Guide([f"Temple {number}" for number in range(1, 5)] + ["Gion Corner"], {"Gion Corner": GION})
    geocoder = _Geocoder(asyncio.TimeoutError())  # every guide place goes unchecked

    result = _run(settings, guide, geocoder)

    assert _names(result) == ["Gion Corner"]
    left_out = [note for note in result.notes if note.startswith("Left out")]
    assert len(left_out) == 1 and left_out[0].startswith("Left out 4 guide places that could not be checked")
    assert "Gion Corner" not in left_out[0]
    gaps = _gaps(result)
    assert len(gaps) == 1 and gaps[0][2].startswith("4 guide places could not be checked"), gaps


def test_a_guide_place_that_is_left_out_is_still_reported_when_the_named_one_is_another(settings: Settings) -> None:
    guide = _Guide(["Nijo Castle", "Gion Corner"], {"Gion Corner": GION})
    geocoder = _Geocoder(None, _near(1))  # "Nijo Castle" is unknown; "Gion Corner" is placed

    result = _run(settings, guide, geocoder)

    assert _names(result) == ["Gion Corner"]
    assert any("could not locate: Nijo Castle" in note for note in result.notes), result.notes


def test_a_place_too_far_away_stays_out_and_stays_reported(settings: Settings) -> None:
    tokyo = GeoLocation(name="Tokyo", geo=GeoPoint(lat=35.68, lng=139.69), source="fake")
    guide = _Guide(["Far Temple", "Near Temple"], {})
    geocoder = _Geocoder(tokyo, _near(1))

    result = _run(settings, guide, geocoder)

    assert _names(result) == ["Near Temple"]
    assert any("could not locate: Far Temple" in note for note in result.notes), result.notes


@pytest.mark.parametrize("spelling", ["Gion Corner Theatre", "gion corner"])
def test_a_named_place_found_under_another_spelling_of_the_same_name_is_cleared_too(settings: Settings, spelling: str) -> None:
    other = GION.model_copy(update={"name": spelling, "aliases": ["Gion Corner"]})
    guide = _Guide(["Gion Corner", "Nijo Castle"], {"Gion Corner": other})
    geocoder = _Geocoder(None, _near(1))

    result = _run(settings, guide, geocoder)

    assert "Gion Corner" in result.payload["wishes"]
    assert not any("could not locate: Gion Corner" in note for note in result.notes), result.notes
