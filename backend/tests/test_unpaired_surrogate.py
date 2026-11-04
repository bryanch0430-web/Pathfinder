"""A JSON string with a lone UTF-16 surrogate escape is refused with 422, not a 500 with a traceback.

A broken input method, or a string cut inside an emoji, can send "\\ud800" with nothing after it. Python reads
such an escape into a string that cannot be written as UTF-8: pydantic refuses it where a field has a limit
(and FastAPI's own answer to that echoed it back and failed), and where a field has none it went on to the
store, which failed on it. The body is refused where every JSON body comes in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import create_app

LONE = "\\ud800"  # the six characters of the JSON escape, as they appear in the request body
GUEST = {"X-Client-Token": "guest-surrogate"}


def _send(client: TestClient, method: str, path: str, body: str, headers: dict[str, str] | None = None):
    return client.request(method, path, content=body, headers={"content-type": "application/json", **(headers or {})})


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def session_id(client: TestClient) -> str:
    return client.post("/api/journeys", json={"client_token": "guest-surrogate"}).json()["session"]["id"]


def _assert_refused(response) -> None:
    assert response.status_code == 422
    assert response.json() == {"detail": "The request contains a character that cannot be read. Please retype that text."}


def test_a_chat_message_with_a_lone_surrogate_is_refused_with_422(client: TestClient) -> None:
    response = _send(client, "post", "/api/turn/stream", f'{{"message": "Swap the hotel {LONE} please", "client_token": "g"}}')

    _assert_refused(response)
    assert client.get("/api/ready").status_code == 200


def test_a_state_patch_with_a_lone_surrogate_is_refused_with_422_and_changes_nothing(
    client: TestClient, session_id: str
) -> None:
    before = client.get(f"/api/journeys/{session_id}", headers=GUEST).json()["context"]

    response = _send(client, "patch", f"/api/journeys/{session_id}/state", f'{{"destination": "Kyoto {LONE}"}}', GUEST)

    _assert_refused(response)
    assert client.get(f"/api/journeys/{session_id}", headers=GUEST).json()["context"] == before


def test_creating_a_trip_with_a_lone_surrogate_in_the_token_is_refused_with_422(client: TestClient) -> None:
    _assert_refused(_send(client, "post", "/api/journeys", f'{{"client_token": "abc{LONE}"}}'))


def test_a_lone_surrogate_anywhere_in_the_body_is_refused(client: TestClient, session_id: str) -> None:
    path = f"/api/journeys/{session_id}/state"

    in_a_list = _send(client, "patch", path, f'{{"preferences": ["food", "quiet{LONE}"]}}', GUEST)
    in_a_name = _send(client, "patch", path, f'{{"{LONE}": 1, "destination": "Kyoto"}}', GUEST)
    after_a_pair = _send(client, "patch", path, f'{{"destination": "Kyoto \\ud83d\\ude00 {LONE}"}}', GUEST)
    pair_cut_apart = _send(client, "patch", path, '{"destination": "\\ude00\\ud83d"}', GUEST)

    for response in (in_a_list, in_a_name, after_a_pair, pair_cut_apart):
        _assert_refused(response)


def test_an_emoji_sent_as_a_surrogate_pair_is_read_as_that_emoji(client: TestClient, session_id: str) -> None:
    response = _send(client, "patch", f"/api/journeys/{session_id}/state", '{"destination": "Kyoto \\ud83d\\ude00"}', GUEST)

    assert response.status_code == 200
    assert response.json()["context"]["destination"] == "Kyoto \U0001F600"


def test_the_letters_of_an_escape_after_an_escaped_backslash_are_only_text(client: TestClient, session_id: str) -> None:
    response = _send(client, "patch", f"/api/journeys/{session_id}/state", '{"destination": "Kyoto \\\\ud800"}', GUEST)

    assert response.status_code == 200
    assert response.json()["context"]["destination"] == "Kyoto \\ud800"


def test_an_ordinary_invalid_request_is_answered_as_before(client: TestClient) -> None:
    response = _send(client, "post", "/api/turn/stream", '{"message": ""}')

    assert response.status_code == 422
    [problem] = response.json()["detail"]
    assert problem["loc"] == ["body", "message"] and problem["type"] == "string_too_short"
    assert problem["input"] == ""


def test_text_in_other_scripts_is_still_echoed_in_an_ordinary_refusal(client: TestClient) -> None:
    response = _send(client, "post", "/api/journeys", '{"client_token": "' + "字" * 130 + '"}')

    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == "字" * 130


def test_a_request_with_no_body_or_a_body_that_is_not_json_is_left_to_the_route(client: TestClient) -> None:
    assert client.get("/api/ready").status_code == 200
    assert _send(client, "post", "/api/journeys", "{not json " + LONE).status_code == 422


# -- a body that is not UTF-8 -------------------------------------------------------------------
# json.loads reads UTF-8, UTF-16 and UTF-32 bodies (and, for a surrogate, the bytes of the surrogate itself), so a
# lone surrogate has to be looked for in the text as it is read, not in the bytes as they were sent.

ENCODINGS = ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"]


def _send_bytes(client: TestClient, method: str, path: str, body: bytes, headers: dict[str, str] | None = None):
    return client.request(method, path, content=body, headers={"content-type": "application/json", **(headers or {})})


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_a_lone_surrogate_in_a_body_in_utf_16_or_utf_32_is_refused(
    client: TestClient, session_id: str, encoding: str
) -> None:
    body = f'{{"destination": "Kyoto {LONE}"}}'.encode(encoding)

    _assert_refused(_send_bytes(client, "patch", f"/api/journeys/{session_id}/state", body, GUEST))


def test_the_chat_stream_and_the_charset_of_the_content_type_make_no_difference(client: TestClient) -> None:
    body = f'{{"message": "Swap the hotel {LONE} please", "client_token": "g"}}'.encode("utf-16")

    refused = client.post("/api/turn/stream", content=body, headers={"content-type": "application/json; charset=utf-16"})

    _assert_refused(refused)
    _assert_refused(_send_bytes(client, "post", "/api/turn/stream", body))


def test_a_lone_surrogate_sent_as_its_own_three_bytes_is_refused(client: TestClient, session_id: str) -> None:
    """The bytes of U+D800 (what a UTF-8 encoder that does not check would write) are read as that surrogate."""
    body = b'{"destination": "Kyoto \xed\xa0\x80"}'

    _assert_refused(_send_bytes(client, "patch", f"/api/journeys/{session_id}/state", body, GUEST))


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_a_body_in_utf_16_or_utf_32_is_still_read_when_it_holds_no_lone_surrogate(
    client: TestClient, session_id: str, encoding: str
) -> None:
    body = '{"destination": "Kyoto \U0001F600 \\ud83d\\ude00"}'.encode(encoding)

    response = _send_bytes(client, "patch", f"/api/journeys/{session_id}/state", body, GUEST)

    assert response.status_code == 200, response.text
    assert response.json()["context"]["destination"] == "Kyoto \U0001F600 \U0001F600"
