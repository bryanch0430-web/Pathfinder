"""A repair that re-types a URL the plan already had is not a repair that brings a URL of its own.

The writer hands the whole plan back, so a URL it copied can come back with the host in capitals, a slash
on the end or a #fragment. That is the original's URL, not a new source, and it must not sink the repair;
a URL with another host, path or query is still a new one and is still refused.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from app.application.validation import _same_url
from app.config import Settings
from tests.test_repair_guard import _accepted, _payload, _plan, _refused

ORIGINAL = "https://Example.com/Tickets"


def _retyped(url: str) -> list[str]:
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    return [f"{scheme.upper()}://{host.upper()}{slash}{path}", url + "/", url + "#details", url + "/#top"]


@pytest.mark.parametrize("copy", _retyped(ORIGINAL))
def test_a_retyped_url_in_a_field_the_guard_overwrites_is_not_refused(settings: Settings, copy: str) -> None:
    plan = _plan(settings)
    plan.days[0].items[0].booking_url = ORIGINAL
    reply = _payload(plan)
    reply["days"][0]["items"][0]["booking_url"] = copy

    repaired = _accepted(settings, plan, reply)

    assert repaired.days[0].items[0].booking_url == ORIGINAL, "the guard puts the original back"


@pytest.mark.parametrize("copy", _retyped(ORIGINAL))
def test_a_retyped_source_url_is_not_refused_and_the_original_source_stays(settings: Settings, copy: str) -> None:
    plan = _plan(settings)
    plan.hotels[0].source_refs[0].url = ORIGINAL
    reply = _payload(plan)
    reply["hotels"][0]["source_refs"][0]["url"] = copy

    repaired = _accepted(settings, plan, reply)

    assert repaired.hotels[0].source_refs[0].url == ORIGINAL


@pytest.mark.parametrize("copy", _retyped("https://example.com/tickets"))
def test_a_retyped_url_in_a_surviving_note_is_not_refused(settings: Settings, copy: str) -> None:
    plan = _plan(settings)
    plan.days[0].items[0].notes = "Tickets: https://example.com/tickets."
    reply = _payload(plan)
    reply["days"][0]["items"][0]["notes"] = f"Tickets: {copy}."

    repaired = _accepted(settings, plan, reply)

    assert repaired.days[0].items[0].notes == f"Tickets: {copy}."


def _other_host(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: https://evil.example/tickets"


def _other_path(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: https://example.com/tickets/vip"


def _other_path_case(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: https://example.com/Tickets"


def _other_query(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: https://example.com/tickets?ref=writer"


def _lookalike_host(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: https://example.com.evil.example/tickets"


def _other_userinfo(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: https://pay@example.com/tickets"


def _other_scheme(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Tickets: http://example.com/tickets"


def _new_booking_url(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["booking_url"] = "https://evil.example/book"


@pytest.mark.parametrize(
    "tamper",
    [_other_host, _other_path, _other_path_case, _other_query, _lookalike_host, _other_userinfo, _other_scheme, _new_booking_url],
)
def test_a_url_that_is_not_a_copy_of_an_original_one_is_still_refused(
    settings: Settings, tamper: Callable[[dict[str, Any]], None]
) -> None:
    plan = _plan(settings)
    plan.days[0].items[0].notes = "Tickets: https://example.com/tickets."
    reply = _payload(plan)
    tamper(reply)

    _refused(settings, plan, reply, "not in the original plan")


@pytest.mark.parametrize(
    "first, second",
    [
        ("https://Example.com/a", "https://example.com/a/"),
        ("HTTP://example.com/a#x", "http://example.com/a"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/a?q=1#f", "https://EXAMPLE.com/a/?q=1"),
    ],
)
def test_urls_that_differ_only_in_case_of_the_host_a_trailing_slash_or_a_fragment_are_the_same(first: str, second: str) -> None:
    assert _same_url(first) == _same_url(second)


@pytest.mark.parametrize(
    "first, second",
    [
        ("https://example.com/a", "https://example.com/A"),
        ("https://example.com/a", "https://example.com/a/b"),
        ("https://example.com/a?q=1", "https://example.com/a?q=2"),
        ("https://example.com/a", "https://example.org/a"),
        ("https://example.com/a", "http://example.com/a"),
        ("https://example.com/a", "https://example.com/a?"),
    ],
)
def test_urls_that_differ_in_anything_else_are_not_the_same(first: str, second: str) -> None:
    assert _same_url(first) != _same_url(second)
