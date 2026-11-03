"""A failing geocoder keeps its failure class from the chain to the plan's data gap.

The production geocoder is a `GeocoderChain`. When every source fails it must still say
how (a timeout, a dropped connection, a refused key) and whether asking again could help,
so the gap on the plan tells the traveller the truth about both.
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
from app.domain.errors import ProviderError, operator_text
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
from app.infrastructure.providers.geocoding import GeocoderChain

KYOTO = GeoPoint(lat=35.0116, lng=135.7681)


class _Source:
    """A geocoder source that fails with `error` or answers with `found`."""

    def __init__(self, name: str, *, error: Exception | None = None, found: GeoLocation | None = None) -> None:
        self.name = name
        self.error = error
        self.found = found

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        if self.error is not None:
            raise self.error
        return self.found


def _failing(name: str, kind: FailureKind, *, retryable: bool = True, detail: str = "") -> _Source:
    error = ProviderError(f"{name} failed", kind=kind, source=name, retryable=retryable, detail=detail)
    return _Source(name, error=error)


def _chain_error(*sources: _Source) -> ProviderError:
    with pytest.raises(ProviderError) as failure:
        asyncio.run(GeocoderChain(list(sources)).geocode("Kyoto"))
    return failure.value


def test_a_chain_whose_sources_all_time_out_reports_a_retryable_timeout() -> None:
    error = _chain_error(_failing("a", FailureKind.TIMEOUT), _failing("b", FailureKind.NETWORK))

    assert error.kind is FailureKind.TIMEOUT
    assert error.retryable
    assert error.source == "geocoder-chain"


def test_a_chain_whose_sources_are_all_refused_is_not_retryable() -> None:
    error = _chain_error(
        _failing("a", FailureKind.AUTH, retryable=False), _failing("b", FailureKind.AUTH, retryable=False)
    )

    assert error.kind is FailureKind.AUTH
    assert not error.retryable


def test_one_failure_that_retrying_could_fix_makes_the_chain_error_retryable() -> None:
    error = _chain_error(_failing("a", FailureKind.AUTH, retryable=False), _failing("b", FailureKind.NETWORK))

    assert error.kind is FailureKind.NETWORK
    assert error.retryable


def test_the_chain_error_names_its_sources_but_not_what_they_said() -> None:
    error = _chain_error(
        _failing("a", FailureKind.TIMEOUT, detail="UPSTREAM-PAGE with key=abc"), _failing("b", FailureKind.NETWORK)
    )

    assert "a" in str(error) and "b" in str(error)
    assert "UPSTREAM-PAGE" not in str(error)
    # What the sources said is kept for the trace.
    assert "UPSTREAM-PAGE" in operator_text(error)


def test_a_chain_that_found_nothing_is_not_a_failure() -> None:
    chain = GeocoderChain([_Source("a"), _Source("b")])

    assert asyncio.run(chain.geocode("Nowhere")) is None


class _Guide:
    """A place source whose listings carry no coordinates, so each one must be geocoded."""

    name = "guide"
    available = True

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names

    async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
        if kwargs.get("category") != "attraction":
            return []
        ref = SourceRef(title="Kyoto guide", url="https://example.com/kyoto", source="guide")
        return [PlaceCandidate(name=name, source_refs=[ref]) for name in self.names]


def _attractions(
    settings: Settings,
    geocoder: object,
    *,
    names: tuple[str, ...] = ("Kinkaku-ji", "Nijo Castle"),
    preferences: list[str] | None = None,
    max_calls: int | None = None,
) -> AgentResult:
    registry = SimpleNamespace(places=[_Guide(names)], notes=[], geocoder=geocoder, web=[], web_deep=[])
    start = date.today() + timedelta(days=30)
    request = AgentContext(
        context=TripContext(
            session_id="s",
            destination="Kyoto",
            start_date=start,
            end_date=start + timedelta(days=2),
            preferences=preferences or [],
        ),
        destination=GeoLocation(name="Kyoto", geo=KYOTO),
        date_range=DateRange(start=start, end=start + timedelta(days=2)),
        request_id="req_outage",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_outage", max_calls=max_calls),
        settings=settings,
    )
    return asyncio.run(AttractionAgent(registry, settings).run(request))


def _gaps(result: AgentResult) -> list[tuple[FailureKind, bool, str]]:
    return [(gap.kind, gap.retryable, gap.message) for gap in result.failures if gap.section == "attractions"]


def test_the_gap_for_a_chain_of_timeouts_says_timeout_and_asks_to_retry(settings: Settings) -> None:
    chain = GeocoderChain([_failing("a", FailureKind.TIMEOUT), _failing("b", FailureKind.NETWORK)])

    gaps = _gaps(_attractions(settings, chain))

    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.TIMEOUT, True)]
    assert "timeout" in gaps[0][2] and "server_error" not in gaps[0][2]


def test_the_gap_for_a_refused_key_is_not_retryable(settings: Settings) -> None:
    chain = GeocoderChain(
        [_failing("a", FailureKind.AUTH, retryable=False), _failing("b", FailureKind.AUTH, retryable=False)]
    )

    gaps = _gaps(_attractions(settings, chain))

    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.AUTH, False)]
