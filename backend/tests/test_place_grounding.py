"""A new destination or departure city is stored only when a gazetteer confirms the name."""

from __future__ import annotations

import asyncio
from datetime import date

from app.application.clarification import Clarifier
from app.application.understanding import _apply_rules, confirm_place, ground_new_places, place_reply
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import Intent, RouteDecision, TripContext, TripStop

TODAY = date(2026, 11, 19)


class _Hit:
    def __init__(self, name: str) -> None:
        self.name = name


class _Map:
    available = True

    def __init__(self, answers: dict[str, str | None] | None = None, fail: Exception | None = None) -> None:
        self.answers = {key.casefold(): value for key, value in (answers or {}).items()}
        self.fail = fail
        self.calls: list[str] = []

    async def geocode(self, name: str, *, country_hint: str = ""):
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail
        found = self.answers.get(name.casefold(), None)
        if found is None and name.casefold() not in self.answers:
            return None
        return None if found is None else _Hit(found)


def _context(**kwargs) -> TripContext:
    return TripContext(session_id="s", **kwargs)


def _ask(status: str, slot: str, name: str) -> str:
    reply, slots = asyncio.run(
        Clarifier(None).ask(  # type: ignore[arg-type]
            message=name,
            context=_context(),
            decision=RouteDecision(
                intent=Intent.UNCLEAR,
                confidence=0.95,
                reason_code=status,
                rationale=name,
                missing_slots=[slot],
            ),
        )
    )
    assert slots == [slot]
    return reply


def test_a_known_city_is_accepted_without_calling_the_map() -> None:
    gazetteer = _Map({"Kyoto": "should not be asked"})
    before = _context()
    context = _context(destination="Kyoto")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert result.first() is None
    assert context.destination == "Kyoto"
    assert gazetteer.calls == []
    assert asyncio.run(confirm_place("Kyoto", gazetteer)).status == "accept"


def test_a_different_name_is_offered_and_not_stored() -> None:
    gazetteer = _Map({"Mars": "Marseille, France"})
    before = _context(destination="Kyoto")
    context = _context(destination="Kyoto", origin="Mars")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert context.origin == ""
    assert context.destination == "Kyoto"
    assert result.first() == ("suggest_place", "origin", "Mars")
    assert result.suggestion_for("origin") == "Marseille, France"
    assert place_reply(result) == (
        "The closest place I can find for Mars is Marseille, France. Is that the one?"
    )


def test_gundam_and_mars_are_both_offered_and_neither_is_stored() -> None:
    gazetteer = _Map({
        "Gundam": "Gundam, Mopidevi Mandal, Andhra Pradesh, India",
        "Mars": "Marseille, France",
    })
    before = _context()
    context = _context(destination="Gundam", origin="Mars")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert context.destination == "" and context.origin == ""
    assert result.first() == ("suggest_place", "destination", "Gundam")
    reply = place_reply(result)
    assert "I found Gundam, Mopidevi Mandal, Andhra Pradesh, India for Gundam." in reply
    assert "The closest place I can find for Mars is Marseille, France." in reply
    assert reply.endswith("Are these the ones?")


def test_a_confirmed_name_is_kept_without_searching_again() -> None:
    gazetteer = _Map(fail=ProviderError("timed out", source="nominatim"))
    before = _context()
    context = _context(origin="Marseille, France")

    result = asyncio.run(ground_new_places(before, context, gazetteer, {"marseille, france"}))

    assert result.first() is None
    assert context.origin == "Marseille, France"
    assert gazetteer.calls == []


def test_a_name_the_map_returns_is_offered_for_confirmation() -> None:
    gazetteer = _Map({"Nagoya": "Nagoya, Japan"})
    before = _context()
    context = _context(destination="Nagoya")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert context.destination == ""
    assert result.first() == ("suggest_place", "destination", "Nagoya")
    assert place_reply(result) == "I found Nagoya, Japan for Nagoya. Is that the one?"


def test_the_same_name_is_not_read_as_a_route() -> None:
    gazetteer = _Map({"Japan": "Japan"})
    before = _context()
    context = _context(destination="Japan")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert context.destination == ""
    assert place_reply(result) == "I found Japan. Is that the one?"


def test_a_name_the_map_does_not_know_asks_for_the_destination() -> None:
    gazetteer = _Map({"Zorpville": None})
    before = _context(budget=8000, party_size=2)
    context = _context(destination="Zorpville", budget=8000, party_size=2)

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert result.first() == ("unknown_place", "destination", "Zorpville")
    assert context.destination == ""
    assert context.budget == 8000 and context.party_size == 2
    assert place_reply(result) == "I can't find a place called Zorpville. Which city should the trip go to?"
    assert _ask("unknown_place", "destination", place_reply(result)) == place_reply(result)


def test_an_unknown_departure_city_asks_where_you_are_leaving_from_and_keeps_the_destination() -> None:
    gazetteer = _Map({"Hell": None})
    before = _context(destination="Kyoto")
    context = _context(destination="Kyoto", origin="Hell")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert result.first() == ("unknown_place", "origin", "Hell")
    assert context.destination == "Kyoto"
    assert context.origin == ""
    assert place_reply(result) == "I can't find a place called Hell. Which city are you leaving from?"


def test_a_map_timeout_does_not_store_the_name() -> None:
    gazetteer = _Map(fail=ProviderError("timed out", source="nominatim"))
    before = _context()
    context = _context(destination="Nagoya")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert result.first() == ("unchecked_place", "destination", "Nagoya")
    assert context.destination == ""
    assert place_reply(result) == "I couldn't check Nagoya just now. Tell me the city again."


def test_a_map_with_no_data_erases_the_name_and_keeps_the_previous_city() -> None:
    gazetteer = _Map(fail=ProviderNoData("no place", source="nominatim"))
    before = _context(destination="Kyoto")
    context = _context(destination="Zorpville")

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert result.first() == ("unknown_place", "destination", "Zorpville")
    assert context.destination == "Kyoto"


def test_heaven_and_hell_are_both_checked_and_the_destination_is_asked_first() -> None:
    gazetteer = _Map({"Heaven": None, "Hell": None})
    context = _context()
    _apply_rules("i want to go to Heaven, from Hell", context, TODAY)

    result = asyncio.run(ground_new_places(_context(), context, gazetteer))

    assert context.destination == "" and context.origin == ""
    assert set(gazetteer.calls) == {"Heaven", "Hell"}
    assert result.first() == ("unknown_place", "destination", "Heaven")


def test_a_real_village_called_hell_is_accepted_when_the_map_echoes_it() -> None:
    gazetteer = _Map({"Hell": "Hell, Norway", "Heaven": None})
    context = _context()
    _apply_rules("i want to go to Heaven, from Hell", context, TODAY)

    result = asyncio.run(ground_new_places(_context(), context, gazetteer))

    assert context.origin == "" and context.destination == ""
    assert result.suggestion_for("origin") == "Hell, Norway"
    assert result.first() == ("unknown_place", "destination", "Heaven")
    assert "Hell, Norway" in place_reply(result)


def test_an_unknown_extra_stop_is_dropped() -> None:
    gazetteer = _Map({"Zorpville": None})
    before = _context(destination="Kyoto", stops=[TripStop(city="Kyoto", nights=2)])
    context = _context(
        destination="Kyoto",
        stops=[TripStop(city="Kyoto", nights=2), TripStop(city="Zorpville", nights=1)],
    )

    result = asyncio.run(ground_new_places(before, context, gazetteer))

    assert [stop.city for stop in context.stops] == ["Kyoto"]
    assert context.destination == "Kyoto"
    assert result.first() == ("unknown_place", "destination", "Zorpville")


def test_yes_or_the_filled_address_confirms_the_suggestion() -> None:
    from app.application.orchestrator import _confirms_pending

    pending = {"slot": "destination", "name": "Heaven, 10A, Villiers Street, Westminster, London"}
    assert _confirms_pending("That's the one.", pending)
    assert _confirms_pending("yes", pending)
    assert _confirms_pending(
        "Here are the missing details — destination: Heaven, 10A, Villiers Street, Westminster, London.",
        pending,
    )
    assert not _confirms_pending("Here are the missing details — destination: Kyoto.", pending)
