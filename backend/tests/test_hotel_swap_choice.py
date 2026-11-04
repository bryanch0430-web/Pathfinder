"""Which hotel a swap picks: a named one, a priced one, and a reply that says when the new stay has no price.

Before, a swap took the first candidate (rated by the source): an unpriced stay lowered the total with no word about
it, and "swap the hotel for the Hilton Kyoto" ended in whichever hotel came first.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.application.modification import ModifyPath
from app.application.orchestrator import _describe_diff
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import GeoPoint, Money, PriceKind, SourceRef, utcnow
from app.evaluation import SandboxHotels
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.hotels import HotelCandidate
from tests.test_modify_targets import _build


def _candidate(base: HotelCandidate, name: str, price: float | None, nudge: int) -> HotelCandidate:
    return HotelCandidate(
        name=name,
        area="Central",
        geo=GeoPoint(lat=base.geo.lat + 0.002 * nudge, lng=base.geo.lng),
        nightly_price=Money(amount=price, currency="USD") if price is not None else None,
        price_kind=PriceKind.REFERENCE if price is not None else PriceKind.NONE,
        rating=4.0,
        review_count=100,
        source_refs=[SourceRef(title=name, url=f"https://example.com/{nudge}", source="sandbox", fetched_at=utcnow())],
    )


class _Hotels(SandboxHotels):
    """The two sandbox hotels (the second one unpriced when ``unpriced_second``) and more of the given (name, price)."""

    def __init__(self, extra: list[tuple[str, float | None]], *, unpriced_second: bool = False) -> None:
        super().__init__("Kyoto")
        self.extra, self.unpriced_second = extra, unpriced_second

    async def search(self, **kwargs: Any):
        rows = await super().search(**kwargs)
        if self.unpriced_second:
            rows[1].nightly_price, rows[1].price_kind = None, PriceKind.NONE
        return rows + [_candidate(rows[0], name, price, index + 1) for index, (name, price) in enumerate(self.extra)]


def _swap(settings: Settings, message: str, source: _Hotels):
    gateway, planner, plan_path, context, plan = _build(settings)
    plan_path.registry.hotels = [source]
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message=message,
            route_parts=["hotel"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return plan, updated, diff


EXTRA = [("Hilton Kyoto", 200.0), ("Ryokan Sakura", 150.0), ("Hostel Nomad", 30.0)]


@pytest.mark.parametrize(
    "message, expected",
    [
        ("swap the hotel for the Hilton Kyoto", "Hilton Kyoto"),
        ("switch to Ryokan Sakura", "Ryokan Sakura"),
        ("change the hotel to Hostel Nomad", "Hostel Nomad"),
        ("swap the hotel for another one", "Kyoto Garden Inn"),
    ],
)
def test_a_hotel_named_as_the_replacement_is_the_one_chosen(settings: Settings, message: str, expected: str) -> None:
    plan, updated, _ = _swap(settings, message, _Hotels(EXTRA))

    assert plan.hotels[0].name == "The Kyoto Grand"
    assert updated.hotels[0].name == expected


def test_a_swap_prefers_a_hotel_with_a_price(settings: Settings) -> None:
    _, updated, _ = _swap(settings, "swap the hotel for another one", _Hotels([("Hilton Kyoto", 200.0)], unpriced_second=True))

    assert updated.hotels[0].name == "Hilton Kyoto"
    assert updated.hotels[0].price_per_night is not None


def test_a_swap_to_a_hotel_with_no_price_says_so(settings: Settings) -> None:
    plan, updated, diff = _swap(settings, "swap the hotel for another one", _Hotels([], unpriced_second=True))

    assert updated.hotels[0].name == "Kyoto Garden Inn" and updated.hotels[0].price_per_night is None
    assert [g.message for g in updated.data_quality.gaps if g.section == "hotels"] == [
        "The new hotel's price was not returned, so the total does not include the stay."
    ]
    assert "Its price was not returned" in _describe_diff(diff, updated)


def test_a_swap_to_a_hotel_with_a_price_adds_no_such_gap(settings: Settings) -> None:
    _, updated, diff = _swap(settings, "swap the hotel for another one", _Hotels([]))

    assert not [g for g in updated.data_quality.gaps if g.section == "hotels"]
    assert "price was not returned" not in _describe_diff(diff, updated)


# -- a hotel named as the one the traveller does not want, or as too dear, is not the one chosen ----------------------


@pytest.mark.parametrize(
    "message, expected",
    [
        ("swap the hotel, not the Hilton Kyoto", "Kyoto Garden Inn"),
        ("swap the hotel but I don't want the Hilton Kyoto", "Kyoto Garden Inn"),
        ("anything but the Hilton Kyoto please swap the hotel", "Kyoto Garden Inn"),
        ("swap the hotel for another one, other than Ryokan Sakura", "Kyoto Garden Inn"),
        ("swap the hotel for something cheaper than Hilton Kyoto", "Hostel Nomad"),
        ("change the hotel, Hilton Kyoto is too expensive", "Kyoto Garden Inn"),
        ("cheaper hotel, the Hilton Kyoto was too dear", "Hostel Nomad"),
        ("swap the hotel to a cheaper one, not Hostel Nomad", "Kyoto Garden Inn"),
        ("swap the hotel, I don't like the Hilton Kyoto", "Kyoto Garden Inn"),
        ("swap the hotel for Ryokan Sakura instead of the Hilton Kyoto", "Ryokan Sakura"),
        ("swap the hotel for the Hilton Kyoto, not Ryokan Sakura", "Hilton Kyoto"),
        ("swap the hotel for the Hilton Kyoto but not the Hostel Nomad", "Hilton Kyoto"),
        ("the Hilton Kyoto is very nice, swap the hotel to it", "Hilton Kyoto"),
        ("swap the hotel for the Hilton Kyoto, it is not too dear", "Hilton Kyoto"),
        ("I am not happy with this hotel so swap the hotel for the Hilton Kyoto", "Hilton Kyoto"),
    ],
)
def test_a_hotel_named_as_the_one_not_wanted_is_not_chosen(settings: Settings, message: str, expected: str) -> None:
    plan, updated, _ = _swap(settings, message, _Hotels(EXTRA))

    assert plan.hotels[0].name == "The Kyoto Grand"
    assert updated.hotels[0].name == expected


@pytest.mark.parametrize(
    "message, expected",
    [
        # a hotel named for a cheaper swap that is dearer than the stay is not chosen; one that is cheaper is
        ("swap the hotel for something cheaper, maybe the Hilton Kyoto", "Hostel Nomad"),
        ("swap the hotel for something cheaper, like Kyoto Garden Inn", "Kyoto Garden Inn"),
        ("swap the hotel for something cheaper, like Ryokan Sakura", "Hostel Nomad"),
        # a word like lower or less in a sentence that names the hotel does not make the swap a cheaper one
        ("swap the hotel for the Hilton Kyoto, on a lower floor please", "Hilton Kyoto"),
        ("swap the hotel for the Hilton Kyoto, I have less time", "Hilton Kyoto"),
    ],
)
def test_a_hotel_named_for_a_cheaper_swap_is_cheaper_than_the_stay(settings: Settings, message: str, expected: str):
    plan, updated, _ = _swap(settings, message, _Hotels(EXTRA))

    assert updated.hotels[0].name == expected
    if "cheaper" in message and "Hilton" not in expected:
        assert updated.hotels[0].price_per_night.amount < plan.hotels[0].price_per_night.amount
