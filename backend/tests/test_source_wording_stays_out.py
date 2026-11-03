"""What a data source or the writer says about its own failure is for the operator, never the traveller.

The traveller reads three things that are built from a failure: the gap a failed data source leaves
in the plan, the error a turn ends with, and the fields of a few read-only routes. Each is checked
with a source that answers in its own words, and the words must stay in ``ProviderError.detail``
(which goes to the trace) and out of everything else.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.agents import _gap
from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.validation import repair_plan
from app.config import Settings
from app.domain.errors import PlanInvalidError, ProviderError
from app.domain.models import EvalCase, FailureKind
from app.eval_efficiency import accepted_plan, case_context
from app.eval_metering import MeteredLLM
from app.infrastructure.http import use_transport
from app.infrastructure.observability import NoopTracer

#: Wording a source could answer with: free text, an address, a key.
WORDS = "UPSTREAM-WORDS key=serp-key-123 see https://example.invalid/account?token=t0ken"


def _answering(payload: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _serpapi_flights(settings: Settings):
    from app.infrastructure.providers.flights import SerpApiFlightSource

    source = SerpApiFlightSource(settings.model_copy(update={"serpapi_key": "k", "http_retries": 0}))
    return source.search(
        origin="HKG", destination="KIX", depart_date=date(2027, 4, 10), return_date=None, party_size=2, currency="USD"
    )


def _serpapi_hotels(settings: Settings):
    from app.infrastructure.providers.hotels import SerpApiHotelSource

    source = SerpApiHotelSource(settings.model_copy(update={"serpapi_key": "k", "http_retries": 0}))
    return source.search(
        destination="Kyoto",
        check_in=date(2027, 4, 10),
        check_out=date(2027, 4, 12),
        party_size=2,
        budget=None,
        currency="USD",
    )


def _bilibili(settings: Settings):
    from app.infrastructure.providers.notes import BilibiliNotesSource

    return BilibiliNotesSource(settings).search("Kyoto", limit=3)


def _mtr(_: Settings):
    from app.infrastructure.providers.hong_kong import parse_line_status

    return _sync(parse_line_status, {"resultCode": 0, "error": {"errorCode": "E1", "errorMsg": WORDS}}, line="TWL", today=date(2027, 4, 10), source="mtr-next-train")


def _amap(_: Settings):
    from app.infrastructure.providers.amap import check_status

    return _sync(check_status, {"status": "0", "info": WORDS, "infocode": "10001"}, "amap")


def _sync(fn, *args, **kwargs):
    async def run():
        return fn(*args, **kwargs)

    return run()


def _writer(settings: Settings):
    from app.infrastructure.llm import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(
        settings.model_copy(update={"llm_api_key": "k", "llm_base_url": "https://writer.invalid/v1", "llm_model": "m"})
    )
    return llm.complete(system="system", user="user", purpose="probe")


CASES = {
    "serpapi-flights": (_serpapi_flights, {"error": WORDS}, 200),
    "serpapi-hotels": (_serpapi_hotels, {"error": WORDS}, 200),
    "bilibili": (_bilibili, {"code": -412, "message": WORDS}, 200),
    "mtr": (_mtr, None, 200),
    "amap": (_amap, None, 200),
    "writer-vendor-error": (_writer, {"error": {"message": WORDS}}, 401),
}


@pytest.mark.parametrize("name", list(CASES))
def test_a_failing_source_keeps_its_own_words_in_the_detail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    call, payload, status = CASES[name]
    transport = _answering(payload or {}, status)
    # The writer adapter opens its own client instead of going through request_json.
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(**{"transport": transport, **kwargs}))

    with use_transport(transport):
        with pytest.raises(ProviderError) as caught:
            asyncio.run(call(settings))

    error = caught.value
    assert "UPSTREAM-WORDS" not in str(error) and "serp-key-123" not in str(error) and "t0ken" not in str(error), str(error)
    assert error.as_dict()["message"] == str(error)
    assert "UPSTREAM-WORDS" in error.detail  # the operator still reads it, in the trace
    assert "UPSTREAM-WORDS" not in json.dumps(error.as_dict())


@pytest.mark.parametrize("kind", [FailureKind.SERVER_ERROR, FailureKind.RATE_LIMIT, FailureKind.NO_DATA])
def test_the_gap_a_failed_source_leaves_in_the_plan_has_no_source_wording(kind: FailureKind) -> None:
    error = ProviderError("SerpApi returned an error", kind=kind, source="serpapi.flights", detail=WORDS)

    gap = _gap("flight", error)

    assert gap.message == "serpapi.flights: SerpApi returned an error"
    assert "UPSTREAM-WORDS" not in gap.model_dump_json()


def test_a_repair_that_fails_does_not_show_the_writers_or_the_validators_text(settings: Settings) -> None:
    plan = asyncio.run(accepted_plan(settings, case_context(EvalCase(id="t", dataset="design", input="", destination="Kyoto"), "s")))
    llm = MeteredLLM(replies={"plan.repair": json.dumps({"not": WORDS})})
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)

    with pytest.raises(PlanInvalidError) as failure:
        asyncio.run(repair_plan(plan, ["day 1: an item has no source"], gateway, settings))

    shown = str(failure.value)
    assert "bounded repair" in shown
    assert "UPSTREAM-WORDS" not in shown and "pydantic" not in shown and "validation error" not in shown, shown


def test_the_handbook_does_not_show_why_the_hybrid_retriever_fell_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.application import guide_hybrid
    from app.interface.api import create_app

    def broken():
        raise OSError("/home/deploy/models/embedder: permission denied")

    monkeypatch.setattr(guide_hybrid, "_index", broken)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/handbook", params={"destination": "Kyoto"}, headers={"X-Client-Token": "guest-hb"})

    assert response.status_code == 200
    assert response.json()["retrieval"]["mode"] == "lexical-fallback"
    assert "permission denied" not in response.text and "/home/deploy" not in response.text
