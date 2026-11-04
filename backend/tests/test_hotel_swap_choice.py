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
