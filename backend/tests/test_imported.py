from __future__ import annotations

import asyncio
from datetime import date

from app.config import Settings
from app.domain.models import (
    GeoPoint,
    ImportedDay,
    ImportedItinerary,
    ImportedStop,
    Money,
    SourceRef,
    TripContext,
)
from app.evidence.models import Evidence
from app.plan.imported import build_imported_plan


class _FakeProviders:
    async def lookup_place(self, name: str, city: str = ""):
        kind = "meal" if "Ramen" in name else "attraction"
        return [
            Evidence(
                ref="temporary",
                kind=kind,  # type: ignore[arg-type]
                name=name,
                address=f"1 {city or 'Japan'} Street",
                geo=GeoPoint(lat=35.0, lng=135.0),
                summary=f"A sourced {name}.",
                source_refs=[SourceRef(title=name, url="https://example.test", source="Test")],
                price=Money(amount=500, currency="JPY"),
                price_kind="reference",
                opening_hours=["09:00-17:00"],
                hours_stated=True,
                suggested_minutes=60,
            )
        ]

    async def hotels(self, city: str, limit: int = 4):
        return [
            Evidence(
                ref="temporary-hotel",
                kind="hotel",
                name="Imported Station Hotel",
                address=f"2 {city or 'Japan'} Street",
                source_refs=[SourceRef(title="Imported Station Hotel", url="https://example.test", source="Test")],
                price=Money(amount=20000, currency="JPY"),
                price_kind="reference",
            )
        ]

    async def locate(self, destination: str):
        return {"geo": None}

    async def weather(self, city: str, geo, start: date, end: date):
        return []


def test_imported_itinerary_builds_a_multi_day_sourced_plan(monkeypatch):
    context = TripContext(
        session_id="import-test",
        destination="Japan (Osaka, Kyoto, Tokyo)",
        start_date="2027-04-10",
        end_date="2027-04-12",
        days=3,
        party_size=2,
        budget=300000,
        currency="JPY",
    )
    itinerary = ImportedItinerary(
        summary="Three imported days.",
        cities=["Osaka", "Kyoto"],
        days=[
            ImportedDay(day=1, city="Osaka", stops=[ImportedStop(name="Osaka Castle", city="Osaka", start="morning")]),
            ImportedDay(day=2, city="Kyoto", stops=[ImportedStop(name="Kiyomizu-dera", city="Kyoto", start="09:30")]),
            ImportedDay(day=3, city="Osaka", stops=[ImportedStop(name="Ramen Shop", city="Osaka", start="19:00", kind="meal")]),
        ],
    )
    built = asyncio.run(
        build_imported_plan(
            providers=_FakeProviders(),  # type: ignore[arg-type]
            settings=Settings(database_path="data/test.db"),
            context=context,
            itinerary=itinerary,
        )
    )
    assert built is not None
    assert len(built.plan.days) == 3
    assert built.plan.item_count() == 3
    assert all(item.source_refs for day in built.plan.days for item in day.items)
    assert built.plan.days[0].items[0].start == "09:30"
    assert built.plan.days[2].items[0].name == "Ramen Shop"


def test_imported_itinerary_survives_context_roundtrip():
    context = TripContext(
        session_id="import-roundtrip",
        destination="Japan",
        imported_itinerary=ImportedItinerary(
            summary="One day.",
            cities=["Osaka"],
            days=[ImportedDay(day=1, city="Osaka", date="2027-04-10", stops=[ImportedStop(name="Osaka Castle")])],
        ),
    )
    restored = TripContext.model_validate(context.model_dump())
    assert restored.imported_itinerary is not None
    assert restored.imported_itinerary.days[0].stops[0].name == "Osaka Castle"


def test_imported_day_accepts_bare_place_names():
    day = ImportedDay.model_validate({"day": 1, "stops": ["Dotonbori", {"name": "Osaka Castle"}]})
    assert [stop.name for stop in day.stops] == ["Dotonbori", "Osaka Castle"]
