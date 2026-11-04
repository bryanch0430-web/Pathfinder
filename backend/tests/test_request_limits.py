"""No request can carry a text, or a body, of any size.

The chat request's locale and stop label and a rating's reason code were plain strings: they are stored with the
message or the rating and sent back with the trip, so a few requests of 20 MB each made one look at the trip an
answer of 500 MB that held the whole service for eight seconds. Every free text of a request has a limit now, and a
body over 1 MiB (far above anything the page sends) is refused before it is read.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import (
    ID_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    LOCALE_MAX_LENGTH,
    REASON_MAX_LENGTH,
    ChatRequest,
    create_app,
)
from app.interface.body_limit import MAX_BODY_BYTES, TOO_LARGE

GUEST = {"X-Client-Token": "guest-limits"}


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def session_id(client: TestClient) -> str:
    return client.post("/api/journeys", json={"client_token": "guest-limits"}).json()["session"]["id"]


# -- the free texts of a request ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "limit"),
    [("locale", LOCALE_MAX_LENGTH), ("scope_item_id", ID_MAX_LENGTH), ("session_id", ID_MAX_LENGTH)],
)
def test_a_chat_text_over_its_limit_is_refused_and_one_at_it_is_not(client: TestClient, field: str, limit: int) -> None:
    refused = client.post("/api/turn/stream", json={"message": "hello", "client_token": "g", field: "x" * (limit + 1)})
    assert refused.status_code == 422, field
    assert refused.json()["detail"][0]["loc"] == ["body", field]

    allowed = ChatRequest(message="hello", **{field: "x" * limit})
    assert len(getattr(allowed, field)) == limit


def test_the_label_of_a_stop_is_cut_not_refused() -> None:
    request = ChatRequest(message="hello", scope_label="Day 1 · " + "字" * 500)

    assert request.scope_label is not None and len(request.scope_label) == LABEL_MAX_LENGTH
    assert ChatRequest(message="hello", scope_label=None).scope_label is None
    assert ChatRequest(message="hello", scope_label="").scope_label == ""


def test_a_rating_reason_over_its_limit_is_refused(client: TestClient, session_id: str) -> None:
    refused = client.post(
        f"/api/journeys/{session_id}/rating", json={"useful": False, "reason_code": "r" * (REASON_MAX_LENGTH + 1)}, headers=GUEST
    )
    assert refused.status_code == 422

    allowed = client.post(
        f"/api/journeys/{session_id}/rating", json={"useful": False, "reason_code": "r" * REASON_MAX_LENGTH}, headers=GUEST
    )
    assert allowed.status_code == 404  # it got past the limits: there is no plan to rate yet
    assert allowed.json() == {"detail": "No plan for this session"}


def test_a_session_id_over_its_limit_is_refused_when_signing_up_and_when_claiming(client: TestClient) -> None:
    signup = client.post(
        "/api/account/signup",
        json={"email": "limits@example.com", "password": "password123", "session_id": "s" * (ID_MAX_LENGTH + 1)},
    )
    claim = client.post("/api/account/attach", json={"session_id": "s" * (ID_MAX_LENGTH + 1), "client_token": "g"})

    assert signup.status_code == 422
    assert claim.status_code == 422


# -- the size of a body ---------------------------------------------------------------------------


def _json_of_size(size: int) -> bytes:
    """A request for a trip whose body is exactly `size` bytes (the unknown field is ignored)."""
    head = '{"client_token": "guest-limits", "pad": "'
    tail = '"}'
    return (head + "p" * (size - len(head) - len(tail)) + tail).encode()


def test_a_body_that_declares_more_than_the_limit_is_refused_before_it_is_read(client: TestClient) -> None:
    response = client.post(
        "/api/journeys", content=_json_of_size(MAX_BODY_BYTES + 1), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json() == {"detail": TOO_LARGE}
    assert response.headers["connection"] == "close"
    assert response.headers["cache-control"] == "private, no-store"  # the answer is as private as any under /api/journeys


def test_a_body_of_exactly_the_limit_is_read(client: TestClient) -> None:
    response = client.post(
        "/api/journeys", content=_json_of_size(MAX_BODY_BYTES), headers={"content-type": "application/json"}
    )

    assert response.status_code == 200


def test_a_body_sent_in_chunks_is_refused_when_it_grows_past_the_limit(client: TestClient) -> None:
    def chunks():
        yield b'{"client_token": "guest-limits", "pad": "'
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"p" * 65536
        yield b'"}'

    response = client.post("/api/journeys", content=chunks(), headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json() == {"detail": TOO_LARGE}


def test_the_service_keeps_answering_after_a_body_was_refused(client: TestClient) -> None:
    client.post("/api/journeys", content=_json_of_size(MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})

    assert client.get("/api/ready").status_code == 200
    assert client.post("/api/journeys", json={"client_token": "guest-limits"}).status_code == 200


def test_the_biggest_trip_form_the_server_accepts_is_far_below_the_body_limit() -> None:
    form = {"preferences": ["字" * 200] * 50, "hard_constraints": ["字" * 200] * 50, "destination": "字" * 120}
    text = json.dumps(form, ensure_ascii=False)

    for encoding in ("utf-8", "utf-16", "utf-32"):
        assert len(text.encode(encoding)) < MAX_BODY_BYTES // 5, encoding


# -- the answer to a request that cannot be read ---------------------------------------------------


@pytest.mark.parametrize("content_type", ["Application/JSON", "APPLICATION/JSON", "application/Json; charset=utf-8"])
def test_a_lone_surrogate_is_refused_whatever_the_case_of_the_media_type(
    client: TestClient, session_id: str, content_type: str
) -> None:
    response = client.patch(
        f"/api/journeys/{session_id}/state",
        content=b'{"destination": "Kyoto \\ud800"}',
        headers={**GUEST, "content-type": content_type},
    )

    assert response.status_code == 422
    assert "cannot be read" in response.json()["detail"]


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/mixed"])
def test_a_body_that_is_not_text_is_a_422_not_a_500_whatever_the_content_type(
    client: TestClient, session_id: str, content_type: str
) -> None:
    body = json.dumps({"destination": "Kyoto 😀"}).encode("utf-16")

    response = client.patch(
        f"/api/journeys/{session_id}/state", content=body, headers={**GUEST, "content-type": content_type}
    )

    assert response.status_code == 422
    assert all({"loc", "msg", "type"} <= set(problem) for problem in response.json()["detail"])
