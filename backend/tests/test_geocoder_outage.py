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
from app.application.asking import AskPath
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import PlanInvalidError, ProviderError, ProviderNoData, operator_text
from app.domain.models import (
    AgentResult,
    ConstraintStatus,
    DateRange,
    EvalCase,
    FailureKind,
    GeoLocation,
    GeoPoint,
    PlaceCandidate,
    SourceRef,
    TripContext,
)
from app.eval_efficiency import case_context, cost_sandbox
from app.infrastructure.llm import NullLLM
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
    guides: list[Any] | None = None,
) -> AgentResult:
    registry = SimpleNamespace(places=guides or [_Guide(names)], notes=[], geocoder=geocoder, web=[], web_deep=[])
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


class _Down:
    """A geocoder that raises `error` for every place it is asked about."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.asked: list[str] = []

    async def geocode(self, name: str, **_: Any) -> GeoLocation | None:
        self.asked.append(name)
        raise self.error


def test_a_no_result_answer_is_an_unknown_place_not_a_failed_geocoder(settings: Settings) -> None:
    geocoder = _Down(ProviderNoData("no result", source="amap-geocoder"))

    result = _attractions(settings, geocoder)

    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.NO_DATA, False)]
    assert "none could be located" in gaps[0][2] and "failed" not in gaps[0][2]
    assert any("could not locate" in note for note in result.notes)


def test_a_spent_tool_budget_is_not_reported_as_a_geocoder_failure(settings: Settings) -> None:
    geocoder = _Down(ValueError("never asked"))

    # The two place searches use up the cap, so every geocoder call is refused.
    result = _attractions(settings, geocoder, max_calls=2)

    gaps = _gaps(result)
    assert geocoder.asked == []
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.BLOCKED, False)]
    assert "geocoder failed" not in gaps[0][2] and "retry" not in gaps[0][2]
    assert "limit" in gaps[0][2]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (ProviderError("refused", kind=FailureKind.AUTH, source="g", retryable=False), FailureKind.AUTH),
        (ValueError("bad payload"), FailureKind.SERVER_ERROR),
        (KeyError("lat"), FailureKind.SERVER_ERROR),
    ],
)
def test_a_failure_that_asking_again_cannot_fix_is_not_told_to_retry(
    settings: Settings, error: BaseException, kind: FailureKind
) -> None:
    result = _attractions(settings, _Down(error))

    gaps = _gaps(result)
    assert [(gap_kind, retryable) for gap_kind, retryable, _ in gaps] == [(kind, False)]
    assert "could not be checked" in gaps[0][2] and "retry" not in gaps[0][2]


def test_a_failure_that_asking_again_could_fix_is_told_to_retry(settings: Settings) -> None:
    result = _attractions(settings, _Down(asyncio.TimeoutError()))

    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.TIMEOUT, True)]
    assert "geocoder failed (timeout); ask again to retry" in gaps[0][2]


class _Script:
    """A geocoder that follows a script: an exception is raised, anything else is returned."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = steps
        self.asked: list[str] = []

    async def geocode(self, name: str, **_: Any) -> GeoLocation | None:
        step = self.steps[min(len(self.asked), len(self.steps) - 1)]
        self.asked.append(name)
        if isinstance(step, BaseException):
            raise step
        return step


def _near(number: int) -> GeoLocation:
    # A few kilometres apart, so places with similar names are not merged into one.
    return GeoLocation(name=f"place {number}", geo=GeoPoint(lat=KYOTO.lat + 0.03 * number, lng=KYOTO.lng), source="fake")


TEMPLES = tuple(f"Temple {number}" for number in range(1, 7))


def test_the_agent_stops_asking_a_geocoder_that_keeps_failing(settings: Settings) -> None:
    geocoder = _Down(asyncio.TimeoutError())

    result = _attractions(settings, geocoder, names=TEMPLES)

    # Two lookups in a row failed: the other four places are not asked about one by one.
    assert len(geocoder.asked) == 2
    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.TIMEOUT, True)]
    assert gaps[0][2].startswith("6 guide places could not be checked")


def test_a_failure_between_answers_does_not_stop_the_agent(settings: Settings) -> None:
    timeout = asyncio.TimeoutError()
    geocoder = _Script([timeout, _near(1), timeout, _near(2), timeout, _near(3)])

    result = _attractions(settings, geocoder, names=TEMPLES)

    assert len(geocoder.asked) == 6
    assert len(result.payload["places"]) == 3
    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.TIMEOUT, True)]
    assert gaps[0][2].startswith("3 guide places could not be checked")


def test_a_named_place_is_not_asked_about_once_the_geocoder_is_known_to_be_down(settings: Settings) -> None:
    geocoder = _Down(asyncio.TimeoutError())

    # "Gion Corner" is both the third guide place and a place the traveller named.
    result = _attractions(settings, geocoder, names=("Temple 1", "Temple 2", "Gion Corner"), preferences=["Gion Corner"])

    assert len(geocoder.asked) == 2
    assert result.payload["wishes"] == {"Gion Corner": None}


class _Finds:
    """A geocoder that locates every place, a few kilometres from the last one."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def geocode(self, name: str, **_: Any) -> GeoLocation | None:
        self.asked.append(name)
        return _near(len(self.asked))


class _WishFails(_Guide):
    """Lists the guide places; searching for the place the traveller named raises `error`."""

    def __init__(self, error: BaseException, names: tuple[str, ...] = ("Kinkaku-ji", "Nijo Castle")) -> None:
        super().__init__(names)
        self.error = error

    async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
        if kwargs.get("query") == "Gion Corner":
            raise self.error
        return await super().search(**kwargs)


class _WishFound(_Guide):
    """Lists the guide places and knows the place the traveller named."""

    async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
        if kwargs.get("query") == "Gion Corner":
            ref = SourceRef(title="Gion Corner", url="https://example.com/gion", source="found")
            return [PlaceCandidate(name="Gion Corner", geo=GeoPoint(lat=KYOTO.lat + 0.2, lng=KYOTO.lng), source_refs=[ref])]
        return await super().search(**kwargs)


def _wish_gaps(result: AgentResult) -> list[tuple[FailureKind, bool, str]]:
    return [gap for gap in _gaps(result) if "you named" in gap[2]]


def test_a_failed_search_for_a_named_place_is_not_reported_as_no_such_place(settings: Settings) -> None:
    guide = _WishFails(asyncio.TimeoutError())

    result = _attractions(settings, _Finds(), preferences=["Gion Corner"], guides=[guide])

    assert result.payload["wishes"] == {"Gion Corner": None}
    evidence = result.payload["wish_failures"]["Gion Corner"]
    assert "timeout" in evidence and "Ask again to retry" in evidence
    gaps = _wish_gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.TIMEOUT, True)]
    assert "Gion Corner" in gaps[0][2] and "ask again to retry" in gaps[0][2]


def test_a_permanent_failure_of_the_search_for_a_named_place_is_not_told_to_retry(settings: Settings) -> None:
    error = ProviderError("refused", kind=FailureKind.AUTH, source="places", retryable=False)

    result = _attractions(settings, _Finds(), preferences=["Gion Corner"], guides=[_WishFails(error)])

    gaps = _wish_gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.AUTH, False)]
    assert "retry" not in gaps[0][2] and "retry" not in result.payload["wish_failures"]["Gion Corner"]


def test_a_named_place_a_fallback_source_knows_leaves_no_failure(settings: Settings) -> None:
    failing = _WishFails(asyncio.TimeoutError())
    failing.name = "first"

    result = _attractions(settings, _Finds(), preferences=["Gion Corner"], guides=[failing, _WishFound(("Kinkaku-ji",))])

    assert result.payload["wishes"] == {"Gion Corner": "Gion Corner"}
    assert result.payload["wish_failures"] == {}
    assert _wish_gaps(result) == []


def test_a_source_that_knows_no_such_place_is_not_a_failure(settings: Settings) -> None:
    result = _attractions(settings, _Finds(), preferences=["Gion Corner"], guides=[_Guide(("Kinkaku-ji",))])

    assert result.payload["wishes"] == {"Gion Corner": None}
    assert result.payload["wish_failures"] == {}
    assert _wish_gaps(result) == []


def test_a_geocoder_failure_for_a_named_place_is_recorded_as_a_failure(settings: Settings) -> None:
    class _FoundWithoutCoordinates(_Guide):
        async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
            if kwargs.get("query") == "Gion Corner":
                ref = SourceRef(title="Gion Corner", url="https://example.com/gion", source="found")
                return [PlaceCandidate(name="Gion Corner", source_refs=[ref])]
            return await super().search(**kwargs)

    # Every place the geocoder is asked about fails; the named place is found, but cannot be placed.
    result = _attractions(
        settings, _Down(asyncio.TimeoutError()), preferences=["Gion Corner"], guides=[_FoundWithoutCoordinates(("Kinkaku-ji",))]
    )

    assert result.payload["wishes"] == {"Gion Corner": None}
    assert "timeout" in result.payload["wish_failures"]["Gion Corner"]
    # The geocoder gap already covers it; it is not reported a second time as a failed lookup.
    assert _wish_gaps(result) == []
    assert any("could not be checked" in message for _, _, message in _gaps(result))


def _include_check(plan, wish: str):
    return next(check for check in plan.constraints if check.text == f"Include {wish}")


def _plan_with_wish(settings: Settings, offline_gateway: LLMGateway, guide: Any):
    registry = cost_sandbox(settings, "Kyoto")
    registry.places = [guide]
    registry.notes = []
    context = case_context(EvalCase(id="wish", dataset="design", input="", destination="Kyoto", days=3), "s")
    context = context.model_copy(update={"preferences": ["Gion Corner"]})

    async def scenario():
        path = PlanPath(planner=Planner(offline_gateway, settings, registry.fx), registry=registry, settings=settings)
        return await path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_wish"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )

    return asyncio.run(scenario())


class _LocatedGuide(_WishFails):
    """A guide whose places carry coordinates, so only the named place's search can fail."""

    async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
        found = await super().search(**kwargs)
        return [place.model_copy(update={"geo": _near(index + 1).geo}) for index, place in enumerate(found)]


def test_the_check_for_a_named_place_says_the_lookup_failed(settings: Settings, offline_gateway: LLMGateway) -> None:
    plan = _plan_with_wish(settings, offline_gateway, _LocatedGuide(asyncio.TimeoutError(), names=("Kinkaku-ji", "Nijo Castle", "Gion")))

    check = _include_check(plan, "Gion Corner")

    assert check.status is ConstraintStatus.UNKNOWN
    assert "No place source returned" not in check.evidence
    assert "timeout" in check.evidence and "Ask again to retry" in check.evidence


def test_the_check_for_a_named_place_no_source_knows_keeps_its_wording(settings: Settings, offline_gateway: LLMGateway) -> None:
    plan = _plan_with_wish(settings, offline_gateway, _LocatedGuide(ProviderNoData("no page", source="guide"), names=("Kinkaku-ji",)))

    check = _include_check(plan, "Gion Corner")

    assert check.status is ConstraintStatus.UNKNOWN
    assert check.evidence == "No place source returned a place with this name."


# -- the destination itself: the plan path and the ask path ----------------------------------------


def _plan_for(settings: Settings, geocoder: object):
    start = date.today() + timedelta(days=30)
    context = TripContext(session_id="s", destination="Kyoto", start_date=start, end_date=start + timedelta(days=2))
    path = PlanPath(planner=None, registry=SimpleNamespace(geocoder=geocoder, fx=None), settings=settings)
    return asyncio.run(
        path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_destination"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )


def test_an_outage_at_the_destination_lookup_is_a_failed_source_not_a_bad_query(settings: Settings) -> None:
    outage = ProviderError("geocoding failed for all sources", kind=FailureKind.TIMEOUT, source="geocoder-chain")

    with pytest.raises(ProviderError) as failure:
        _plan_for(settings, _Down(outage))

    assert failure.value.kind is FailureKind.TIMEOUT and failure.value.retryable
    assert not isinstance(failure.value, PlanInvalidError)


@pytest.mark.parametrize("geocoder", [_Script([None]), _Down(ProviderNoData("no result", source="amap-geocoder"))])
def test_a_destination_no_map_source_knows_is_a_name_to_rephrase(settings: Settings, geocoder: object) -> None:
    with pytest.raises(PlanInvalidError) as failure:
        _plan_for(settings, geocoder)

    assert "Try the city and country together" in str(failure.value)


class _Forecast:
    name = "forecast"
    available = True

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    async def forecast(self, *, geo: GeoLocation, start: date, end: date) -> list[Any]:
        if self.error is not None:
            raise self.error
        return []


def _weather_answer(settings: Settings, geocoder: object, weather: _Forecast | None = None) -> str:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    registry = SimpleNamespace(weather=[weather or _Forecast()], geocoder=geocoder, routes=[])
    start = date.today() + timedelta(days=30)
    context = TripContext(session_id="s", destination="Kyoto", start_date=start, end_date=start + timedelta(days=2))
    text, _related, _source = asyncio.run(
        AskPath(gateway=gateway, registry=registry, settings=settings).answer(
            question="What will the weather be like?",
            plan=None,
            context=context,
            toolbox=Toolbox(path="ask", tracer=NoopTracer(), store=None, request_id="req_ask"),
            emit=lambda *_: None,
        )
    )
    return text


def test_a_weather_question_says_when_the_lookup_failed(settings: Settings) -> None:
    text = _weather_answer(settings, _Down(ProviderError("down", kind=FailureKind.TIMEOUT, source="geocoder-chain")))

    assert "failed (timeout)" in text and "Asking again later may work" in text
    assert "No forecast could be found" not in text


def test_a_weather_question_about_an_unknown_place_says_none_was_found(settings: Settings) -> None:
    for geocoder in (_Script([None]), _Down(ProviderNoData("no result", source="amap-geocoder"))):
        text = _weather_answer(settings, geocoder)

        assert "No forecast could be found" in text
        assert "failed" not in text


def test_a_weather_question_says_when_the_forecast_source_failed(settings: Settings) -> None:
    text = _weather_answer(settings, _Finds(), _Forecast(asyncio.TimeoutError()))

    assert "failed (timeout)" in text and "Asking again later may work" in text


def test_a_weather_failure_that_asking_again_cannot_fix_is_not_told_to_retry(settings: Settings) -> None:
    refused = ProviderError("refused", kind=FailureKind.AUTH, source="geocoder-chain", retryable=False)

    text = _weather_answer(settings, _Down(refused))

    assert "failed (auth)" in text and "Asking again" not in text


# -- mixed outcomes: each failure is reported, each place counted once -------------------------------


def test_a_permanent_failure_is_not_hidden_behind_an_earlier_retryable_one(settings: Settings) -> None:
    timeout = asyncio.TimeoutError()
    refused = ProviderError("refused", kind=FailureKind.AUTH, source="geocoder-chain", retryable=False)
    # Failures with an answer between them, so the agent keeps asking.
    geocoder = _Script([timeout, _near(1), refused, _near(2)])

    result = _attractions(settings, geocoder, names=("Temple 1", "Temple 2", "Temple 3", "Temple 4"))

    assert len(geocoder.asked) == 4
    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.TIMEOUT, True), (FailureKind.AUTH, False)]
    assert gaps[0][2] == "1 guide place could not be checked on the map because the geocoder failed (timeout); ask again to retry."
    assert gaps[1][2] == "1 guide place could not be checked on the map because the geocoder failed (auth)."


def test_the_none_located_gap_counts_every_place_the_guide_named(settings: Settings) -> None:
    timeout = asyncio.TimeoutError()
    # One place the geocoder does not know, then two lookups in a row that fail; the fourth is not asked.
    geocoder = _Script([None, timeout, timeout])

    result = _attractions(settings, geocoder, names=("Temple 1", "Temple 2", "Temple 3", "Temple 4"))

    assert len(geocoder.asked) == 3
    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.NO_DATA, False), (FailureKind.TIMEOUT, True)]
    assert gaps[0][2] == "The guide named 4 places, but none could be located on the map."
    assert gaps[1][2].startswith("3 guide places could not be checked")


def test_a_named_place_the_guide_list_failed_on_is_counted_once(settings: Settings) -> None:
    result = _attractions(settings, _Down(asyncio.TimeoutError()), names=("Kinkaku-ji", "Nijo Castle"), preferences=["Kinkaku-ji"])

    gaps = _gaps(result)
    assert [message[:2] for _, _, message in gaps] == ["2 "], gaps
    assert sum("Kinkaku-ji" in note for note in result.notes) == 1


def test_a_place_the_unknown_answer_was_given_for_twice_is_counted_once(settings: Settings) -> None:
    result = _attractions(settings, _Script([None]), names=("Gion Corner",), preferences=["Gion Corner"])

    gaps = _gaps(result)
    assert gaps[0][2] == "The guide named 1 place, but none could be located on the map."
    assert sum("Gion Corner" in note for note in result.notes) == 1


def test_a_place_located_after_it_failed_is_no_longer_reported_as_unchecked(settings: Settings) -> None:
    # The guide list fails on it once; the lookup for the place the traveller named then answers.
    geocoder = _Script([asyncio.TimeoutError(), _near(1)])

    result = _attractions(settings, geocoder, names=("Kinkaku-ji",), preferences=["Kinkaku-ji"])

    assert [place["name"] for place in result.payload["places"]] == ["Kinkaku-ji"]
    assert result.payload["places"][0]["tags"][0] == "requested"
    assert _gaps(result) == []
    assert not any("could not be checked" in note for note in result.notes)


def test_the_gap_for_a_named_place_reads_naturally(settings: Settings) -> None:
    result = _attractions(settings, _Finds(), preferences=["Gion Corner"], guides=[_WishFails(asyncio.TimeoutError())])

    gaps = _wish_gaps(result)
    assert gaps[0][2] == (
        "The place you named, Gion Corner, could not be looked up because a place source failed (timeout); ask again to retry."
    )


# -- what the agent's chip says ----------------------------------------------------------------------


def test_the_summary_of_an_agent_whose_geocoder_failed_does_not_say_no_source_answered(settings: Settings) -> None:
    result = _attractions(settings, _Down(asyncio.TimeoutError()))

    assert result.status.value == "unavailable"
    assert result.notes[0] == "2 guide places could not be checked on the map."
    assert "No attraction source" not in " ".join(result.notes)


def test_the_summary_of_an_agent_whose_guide_places_could_not_be_located_says_so(settings: Settings) -> None:
    result = _attractions(settings, _Script([None]))

    assert result.notes[0] == "The guide named 2 places, but none could be located on the map."


def test_the_summary_still_says_so_when_no_source_listed_a_place(settings: Settings) -> None:
    result = _attractions(settings, _Down(asyncio.TimeoutError()), names=())

    assert result.notes[0] == "No attraction source returned usable data."


# -- a geocoder route that answers HTTP 404 -----------------------------------------------------------


def _real_chain(settings: Settings) -> GeocoderChain:
    from app.infrastructure.providers.geocoding import NominatimGeocoder, OpenMeteoGeocoder

    return GeocoderChain([OpenMeteoGeocoder(settings), NominatimGeocoder(settings)])


def _answering(status: int, body: Any = None):
    import httpx

    from app.infrastructure.http import use_transport

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {})

    return use_transport(httpx.MockTransport(handler))


def test_a_geocoder_route_that_answers_not_found_is_a_failed_source(settings: Settings) -> None:
    with _answering(404):
        with pytest.raises(ProviderError) as failure:
            asyncio.run(_real_chain(settings).geocode("Kyoto"))

    assert not isinstance(failure.value, ProviderNoData)
    assert failure.value.kind is FailureKind.SERVER_ERROR and not failure.value.retryable


def test_a_gone_geocoder_route_reads_as_an_outage_not_as_places_that_do_not_exist(settings: Settings) -> None:
    with _answering(404):
        result = _attractions(settings, _real_chain(settings))

    gaps = _gaps(result)
    assert [(kind, retryable) for kind, retryable, _ in gaps] == [(FailureKind.SERVER_ERROR, False)]
    assert "could not be checked" in gaps[0][2] and "none could be located" not in gaps[0][2]


def test_a_geocoder_that_answers_with_no_results_still_means_no_such_place(settings: Settings) -> None:
    with _answering(200, {"results": []}):
        assert asyncio.run(_real_chain(settings).geocode("Nowhere")) is None
