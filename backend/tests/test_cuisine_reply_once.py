"""The reply to a cuisine request that found no restaurant says once that the stop stayed as it was.

Before: "No verified japanese restaurant came back, so Kyoto Kitchen stayed as it was, so that stop stayed as it was."
"""

from __future__ import annotations

from app.config import Settings
from tests.test_modify_dates import Trip, reply_of, run_trip


def test_the_cuisine_reply_does_not_say_stayed_as_it_was_twice(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn("I want Japanese food for dinner on day 1, change Kyoto Kitchen")

        reply = reply_of(events)
        assert reply.count("stayed as it was") == 1, reply
        assert reply.endswith("stayed as it was."), reply

    run_trip(settings, scenario)


def test_the_other_outcome_replies_say_it_once_too(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        for message in ("Replace Gion with the Zzyzx Tower", "swap the kiyomizu temple for something else, a far place"):
            reply = reply_of(await trip.turn(message))

            assert reply.count("stayed as it was") <= 1, reply

    run_trip(settings, scenario)
