"""A plan file from another computer opens as a new trip only after it is cleaned: this app's schema, a size cap, no
locks or confirmations, web links only, and the derived data (links, ranks, airport transfers) built here."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_plan, make_two_city_plan
from test_airport_transfers import AirportGeocoder, FakeRoutes, _flights

from app.config import Settings
from app.domain.models import Link, TripPlan
from app.interface.api import create_app

GUEST = {"X-Client-Token": "guest-import"}
URL_KEYS = {"url", "booking_url", "link", "website", "thumbnail_url"}


def _import(client: TestClient, body: dict):
    return client.post(
        "/api/journeys/import",
        headers=GUEST,
        json={"client_token": "guest-import", "plan": body},
    )


def _urls(node, found=None) -> list[str]:
    found = [] if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in URL_KEYS and isinstance(value, str) and value:
                found.append(value)
            else:
                _urls(value, found)
    elif isinstance(node, list):
        for value in node:
            _urls(value, found)
    return found


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_a_file_over_one_megabyte_is_refused_with_413(client: TestClient) -> None:
    body = make_plan(days=1, with_tickets=False).model_dump(mode="json")
    body["days"][0]["items"][0]["notes"] = "x" * 1_100_000

    response = _import(client, body)

    assert response.status_code == 413


@pytest.mark.parametrize("version", [None, "0.1", "2.0", 1])
def test_a_file_that_does_not_carry_this_apps_schema_version_is_refused_with_422(
    client: TestClient, version
) -> None:
    body = make_plan(days=1, with_tickets=False).model_dump(mode="json")
    if version is None:
        del body["schema_version"]
    else:
        body["schema_version"] = version

    response = _import(client, body)

    assert response.status_code == 422
    assert "version" in response.json()["detail"].lower()


def test_a_file_that_does_not_validate_is_refused_with_422(client: TestClient) -> None:
    body = make_plan(days=1, with_tickets=False).model_dump(mode="json")
    body["days"][0]["items"][0]["start"] = 12

    assert _import(client, body).status_code == 422


def test_every_locked_and_confirmed_flag_is_cleared(client: TestClient) -> None:
    plan = make_two_city_plan()
    plan.days[0].items[0].locked = True
    plan.days[1].items[0].confirmed = True
    plan.hotels[0].locked = plan.hotels[1].confirmed = True
    plan.legs[1].locked = True
    body = plan.model_dump(mode="json")

    response = _import(client, body)

    assert response.status_code == 200, response.text
    imported = TripPlan.model_validate(response.json()["plan"])
    assert not any(item.locked or item.confirmed for _, item in imported.iter_items())
    assert not any(
        stay.locked or stay.confirmed
        for stay in [*imported.hotels, *imported.hotel_options]
    )
    assert not any(leg.locked for leg in imported.legs)


def test_only_web_links_are_kept_and_the_links_are_built_again_here(
    client: TestClient,
) -> None:
    plan = make_plan(days=1, with_tickets=False)
    stop = plan.days[0].items[0]
    stop.booking_url = "https://example.com/book"
    stop.source_refs[0].url = "javascript:alert(1)"
    stop.links = [Link(kind="official", url="https://elsewhere.example/stale")]
    plan.hotels[0].booking_url = "data:text/html,<b>x</b>"
    body = plan.model_dump(mode="json")

    response = _import(client, body)

    assert response.status_code == 200, response.text
    data = response.json()["plan"]
    assert all(
        url.lower().startswith(("http://", "https://")) for url in _urls(data)
    ), _urls(data)
    imported = TripPlan.model_validate(data)
    links = imported.days[0].items[0].links
    assert [(link.kind, link.url) for link in links if link.kind == "booking"] == [
        ("booking", "https://example.com/book")
    ]
    assert "https://elsewhere.example/stale" not in [link.url for link in links]
    assert imported.hotels[0].booking_url == ""


def test_the_ranks_are_worked_out_again_here(client: TestClient) -> None:
    plan = make_two_city_plan()
    plan.hotel_options = [
        plan.hotels[0].model_copy(update={"id": "opt_a", "rank": 77}),
        plan.hotels[0].model_copy(
            update={"id": "opt_b", "rank": 88, "name": "Other Inn"}
        ),
    ]
    body = plan.model_dump(mode="json")

    response = _import(client, body)

    assert response.status_code == 200, response.text
    ranks = [stay["rank"] for stay in response.json()["plan"]["hotel_options"]]
    assert 77 not in ranks and 88 not in ranks


def test_the_airport_transfers_are_worked_out_again_here(client: TestClient) -> None:
    container = client.app.state.container
    container.registry.geocoder = AirportGeocoder()
    container.registry.routes = [FakeRoutes()]
    plan = _flights(make_two_city_plan())
    body = plan.model_dump(mode="json")
    assert not [leg for leg in plan.legs if leg.kind in ("to_stay", "from_stay")]

    response = _import(client, body)

    assert response.status_code == 200, response.text
    kinds = sorted(
        leg["kind"]
        for leg in response.json()["plan"]["legs"]
        if leg["kind"] in ("to_stay", "from_stay")
    )
    assert kinds == ["from_stay", "to_stay"]
