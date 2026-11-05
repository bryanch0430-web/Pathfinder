"""Every API call the frontend makes must exist on the FastAPI app.

The backend is the contract. This reads the `/api/...` paths and HTTP methods
used under frontend/src and checks each against the app's routes, so a route
renamed on either side fails here instead of 404-ing in the browser.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.models import GeoPoint
from app.interface.api import create_app
from scripts.seed_platform_demo import DESTINATIONS, _plan

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: A path literal opens with a quote, a backtick or `${BASE}` and runs while the
#: characters are path characters or `${...}` placeholders.
_CALL = re.compile(r"""(?:["'`]|\$\{BASE\})(/api/(?:[A-Za-z0-9_\-./]|\$\{[^}]*\})*)""")
_METHOD = re.compile(r"""method:\s*["'](GET|POST|PUT|PATCH|DELETE)["']""")
_PLACEHOLDER = re.compile(r"\$\{[^}]*\}")
_PARAM = re.compile(r"\{[^}]*\}")


def normalise_frontend(raw: str) -> str:
    """`/api/journeys/${id}/plan${query}` -> `/api/journeys/{}/plan`."""
    segments: list[str] = []
    for segment in raw.split("/"):
        if _PLACEHOLDER.fullmatch(segment):
            segments.append("{}")  # a whole segment is a path parameter
        else:
            segments.append(_PLACEHOLDER.sub("", segment))  # a trailing one is a query suffix
    return "/".join(segments).rstrip("/")


def frontend_calls(source: str) -> set[tuple[str, str]]:
    """(METHOD, normalised path) for every `/api/...` literal in a TS source.

    The method is the `method: "..."` in the same statement, else GET.
    """
    calls: set[tuple[str, str]] = set()
    for match in _CALL.finditer(source):
        end = source.find(";", match.end())
        statement = source[match.end() : end if end != -1 else len(source)]
        method = _METHOD.search(statement)
        calls.add((method.group(1) if method else "GET", normalise_frontend(match.group(1))))
    return calls


def backend_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, _PARAM.sub("{}", route.path))
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_parser_reads_methods_path_params_and_query_suffixes() -> None:
    source = """
    return request(`/api/journeys/${sessionId}/plan${query}`);
    return request(`/api/journeys/${sessionId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ useful, stars }),
    });
    return request(`/api/popular${suffix ? `?${suffix}` : ""}`);
    return request(`/api/traveller?client_token=${encodeURIComponent(token)}`);
    return `${BASE}/api/eval/trials/${runId}/export`;
    """
    assert frontend_calls(source) == {
        ("GET", "/api/journeys/{}/plan"),
        ("POST", "/api/journeys/{}/feedback"),
        ("GET", "/api/popular"),
        ("GET", "/api/traveller"),
        ("GET", "/api/eval/trials/{}/export"),
    }


#: Field names of the TS types the callers read (frontend/src/design-system/types.ts, lib/api.ts).
TS_TRIP_CONTEXT = {
    "session_id", "destination", "origin", "start_date", "end_date", "days", "party_size",
    "budget", "currency", "hotel_style", "pace", "preferences", "hard_constraints", "language",
}
TS_MAP_LEG = {"from_id", "to_id", "mode", "minutes", "km", "path", "source", "steps"}


def test_renamed_routes_accept_the_frontend_payloads_and_return_its_shapes(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "guest-shape"}).json()["session"]["id"]
        # request() in api.ts sends the guest token on every call.
        guest = {"X-Client-Token": "guest-shape"}

        # patchContext(): the body saveContext() builds in usePathfinder.ts.
        patched = client.patch(
            f"/api/journeys/{session_id}/state",
            json={
                "destination": "Kyoto",
                "origin": "Hong Kong",
                "start_date": "2027-04-10",
                "end_date": "2027-04-12",
                "party_size": 2,
                "budget": None,
                "currency": "JPY",
                "hotel_style": "",
                "preferences": ["food"],
                "hard_constraints": [],
            },
            headers=guest,
        )
        assert patched.status_code == 200, patched.text
        assert TS_TRIP_CONTEXT <= set(patched.json()["context"])
        assert patched.json()["context"]["days"] == 3

        place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
        plan = _plan(random.Random(1), place, 1).model_copy(update={"session_id": session_id})
        for index, item in enumerate(plan.days[0].items):
            item.geo = GeoPoint(lat=35.0 + index * 0.01, lng=135.75 + index * 0.01)
        client.app.state.container.store.save_plan(plan, "test plan")

        # getPlan(id, version)
        itinerary = client.get(f"/api/journeys/{session_id}/itinerary", params={"version": plan.version}, headers=guest)
        assert itinerary.status_code == 200, itinerary.text
        assert itinerary.json()["plan"]["trip_id"] == plan.trip_id

        # postFeedback(id, useful, stars, clientToken)
        rated = client.post(
            f"/api/journeys/{session_id}/rating",
            json={"useful": True, "stars": 4, "client_token": "guest-shape"},
            headers=guest,
        )
        assert rated.status_code == 200, rated.text
        assert isinstance(rated.json()["feedback"]["id"], str)
        assert isinstance(rated.json()["preferences_added"], list)

        # getLegs(id, day): reads `legs` as MapLeg[]
        segments = client.get(f"/api/journeys/{session_id}/segments", params={"day": plan.days[0].date.isoformat()}, headers=guest)
        assert segments.status_code == 200, segments.text
        legs = segments.json()["legs"]
        assert legs
        assert all(set(leg) == TS_MAP_LEG for leg in legs)
        assert all(all(len(point) == 2 for point in leg["path"]) for leg in legs)


def test_every_frontend_api_call_exists_on_the_backend(settings: Settings) -> None:
    if not FRONTEND_SRC.exists():
        pytest.skip("frontend/src is not checked out")
    calls: dict[tuple[str, str], str] = {}
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        for call in frontend_calls(path.read_text(encoding="utf-8")):
            calls.setdefault(call, path.relative_to(FRONTEND_SRC).as_posix())
    # The scan must really see the API client, or the check would pass vacuously.
    assert ("POST", "/api/turn/stream") in calls
    assert ("GET", "/api/journeys/{}") in calls
    assert len(calls) >= 25

    routes = backend_routes(create_app(settings))
    missing = sorted(f"{method} {path}  ({source})" for (method, path), source in calls.items() if (method, path) not in routes)
    assert not missing, "frontend calls with no backend route:\n" + "\n".join(missing)
