"""Seed a platform-sized trending corpus: many travellers, many kept journeys.

The rail is only interesting when it has data. This script builds realistic
`SavedTrip` rows (a valid `TripPlan` each) for a list of destinations, plus
anonymous preference profiles, so the retrieval path in `trending_rag.py` can be
demonstrated at scale:

    cd backend
    PYTHONPATH=. python scripts/seed_platform_demo.py --trips 800 --profiles 120
    PYTHONPATH=. python scripts/seed_platform_demo.py --reset

Every synthetic row is prefixed (`demo:` trip ids, `demo-user-` client tokens)
so `--reset` removes exactly what this script created and nothing else.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.memory import embed  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.domain.models import (  # noqa: E402
    CostBreakdown,
    DateRange,
    DayPlan,
    Destination,
    GeoPoint,
    HotelStay,
    ItemType,
    Money,
    PlanItem,
    PreferenceProfile,
    SavedTrip,
    SourceRef,
    TripPlan,
    new_id,
    utcnow,
)
from app.infrastructure.store import SQLiteStore  # noqa: E402

TRIP_PREFIX = "demo:"
PROFILE_PREFIX = "demo-user-"

DESTINATIONS = [
    {
        "name": "Tokyo", "code": "JP", "lat": 35.6762, "lng": 139.6503, "currency": "JPY",
        "areas": ["Shinjuku", "Asakusa"],
        "sights": [
            ("Senso-ji", ["temples", "culture"]), ("Meiji Jingu", ["temples", "nature"]),
            ("Shibuya Crossing", ["city"]), ("TeamLab Planets", ["museums", "art"]),
            ("Ueno Park", ["nature", "museums"]), ("Tsukiji Outer Market", ["food", "markets and shopping"]),
            ("Tokyo Skytree", ["city"]), ("Akihabara", ["markets and shopping", "nightlife"]),
            ("Shinjuku Gyoen", ["nature"]),
        ],
    },
    {
        "name": "Kyoto", "code": "JP", "lat": 35.0116, "lng": 135.7681, "currency": "JPY",
        "areas": ["Gion", "Arashiyama"],
        "sights": [
            ("Fushimi Inari", ["temples", "nature"]), ("Kiyomizu-dera", ["temples"]),
            ("Arashiyama Bamboo Grove", ["nature"]), ("Kinkaku-ji", ["temples"]),
            ("Nishiki Market", ["food", "markets and shopping"]), ("Gion", ["culture", "nightlife"]),
            ("Philosopher's Path", ["nature"]), ("Nijo Castle", ["culture"]),
            ("Kyoto National Museum", ["museums"]),
        ],
    },
    {
        "name": "Hong Kong", "code": "HK", "lat": 22.3193, "lng": 114.1694, "currency": "HKD",
        "areas": ["Central", "Tsim Sha Tsui"],
        "sights": [
            ("Victoria Peak", ["nature", "city"]), ("Star Ferry", ["city"]),
            ("Tian Tan Buddha", ["temples"]), ("Tsim Sha Tsui Promenade", ["city", "nightlife"]),
            ("Mong Kok Markets", ["markets and shopping"]), ("PMQ", ["markets and shopping", "art"]),
            ("Temple Street Night Market", ["food", "nightlife"]),
            ("Hong Kong Museum of History", ["museums"]), ("Repulse Bay", ["nature"]),
        ],
    },
    {
        "name": "Singapore", "code": "SG", "lat": 1.3521, "lng": 103.8198, "currency": "SGD",
        "areas": ["Marina Bay", "Chinatown"],
        "sights": [
            ("Gardens by the Bay", ["nature"]), ("Marina Bay Sands", ["city", "nightlife"]),
            ("Sentosa", ["theme parks"]), ("Chinatown", ["food", "markets and shopping"]),
            ("Little India", ["food", "culture"]), ("National Gallery", ["museums"]),
            ("Botanic Gardens", ["nature"]), ("Clarke Quay", ["nightlife"]),
        ],
    },
    {
        "name": "Taipei", "code": "TW", "lat": 25.0330, "lng": 121.5654, "currency": "TWD",
        "areas": ["Xinyi", "Zhongshan"],
        "sights": [
            ("Taipei 101", ["city"]), ("Elephant Mountain", ["nature"]),
            ("Shilin Night Market", ["food", "nightlife"]), ("National Palace Museum", ["museums"]),
            ("Beitou Hot Springs", ["nature"]), ("Ximending", ["markets and shopping", "nightlife"]),
            ("Chiang Kai-shek Memorial", ["culture"]), ("Raohe Street Market", ["food"]),
        ],
    },
    {
        "name": "Seoul", "code": "KR", "lat": 37.5665, "lng": 126.9780, "currency": "KRW",
        "areas": ["Jongno", "Hongdae"],
        "sights": [
            ("Gyeongbokgung", ["culture", "temples"]), ("Bukchon Hanok Village", ["culture"]),
            ("Myeongdong", ["markets and shopping", "food"]), ("Namsan Tower", ["city"]),
            ("Hongdae", ["nightlife"]), ("Insadong", ["markets and shopping", "art"]),
            ("Han River Park", ["nature"]), ("Dongdaemun", ["markets and shopping"]),
        ],
    },
    {
        "name": "Bangkok", "code": "TH", "lat": 13.7563, "lng": 100.5018, "currency": "THB",
        "areas": ["Sukhumvit", "Rattanakosin"],
        "sights": [
            ("Grand Palace", ["culture", "temples"]), ("Wat Pho", ["temples"]),
            ("Chatuchak Market", ["markets and shopping"]), ("Khao San Road", ["nightlife"]),
            ("Chao Phraya boat", ["city"]), ("Jim Thompson House", ["museums"]),
            ("Lumphini Park", ["nature"]), ("Chinatown", ["food"]),
        ],
    },
    {
        "name": "London", "code": "GB", "lat": 51.5074, "lng": -0.1278, "currency": "GBP",
        "areas": ["Shoreditch", "South Bank"],
        "sights": [
            ("British Museum", ["museums"]), ("Tower of London", ["culture"]),
            ("Borough Market", ["food", "markets and shopping"]), ("Hyde Park", ["nature"]),
            ("Tate Modern", ["museums"]), ("Covent Garden", ["markets and shopping"]),
            ("Camden Market", ["markets and shopping", "nightlife"]), ("Westminster", ["culture"]),
        ],
    },
    {
        "name": "Paris", "code": "FR", "lat": 48.8566, "lng": 2.3522, "currency": "EUR",
        "areas": ["Le Marais", "Latin Quarter"],
        "sights": [
            ("Louvre", ["museums"]), ("Eiffel Tower", ["city"]), ("Montmartre", ["culture"]),
            ("Latin Quarter", ["food"]), ("Musee d'Orsay", ["museums"]),
            ("Luxembourg Gardens", ["nature"]), ("Le Marais", ["markets and shopping", "food"]),
            ("Seine walk", ["city"]),
        ],
    },
    {
        "name": "New York", "code": "US", "lat": 40.7128, "lng": -74.0060, "currency": "USD",
        "areas": ["Midtown", "Brooklyn"],
        "sights": [
            ("Metropolitan Museum", ["museums"]), ("Central Park", ["nature"]),
            ("Times Square", ["city", "nightlife"]), ("Brooklyn Bridge", ["city"]),
            ("Chelsea Market", ["food", "markets and shopping"]), ("MoMA", ["museums"]),
            ("High Line", ["nature"]), ("Greenwich Village", ["food", "nightlife"]),
        ],
    },
    {
        "name": "Sydney", "code": "AU", "lat": -33.8688, "lng": 151.2093, "currency": "AUD",
        "areas": ["Darling Harbour", "The Rocks"],
        "sights": [
            ("Opera House", ["culture"]), ("Bondi Beach", ["nature"]),
            ("Harbour Bridge", ["city"]), ("The Rocks", ["markets and shopping", "food"]),
            ("Taronga Zoo", ["theme parks"]), ("Royal Botanic Garden", ["nature"]),
            ("Manly Ferry", ["city"]), ("Newtown", ["food", "nightlife"]),
        ],
    },
    {
        "name": "Barcelona", "code": "ES", "lat": 41.3874, "lng": 2.1686, "currency": "EUR",
        "areas": ["Eixample", "Gothic Quarter"],
        "sights": [
            ("Sagrada Familia", ["culture"]), ("Park Guell", ["nature", "art"]),
            ("La Rambla", ["markets and shopping", "city"]), ("Gothic Quarter", ["culture"]),
            ("Casa Batllo", ["museums"]), ("Barceloneta Beach", ["nature"]),
            ("Boqueria Market", ["food", "markets and shopping"]), ("Montjuic", ["nature"]),
        ],
    },
]

TRAVELLER_TAGS = [
    "museums", "temples", "nature", "food", "markets and shopping", "nightlife",
    "theme parks", "art", "slow pace", "fast pace", "vegetarian", "luxury stays",
]

HOTEL_STYLES = ["budget", "midrange", "boutique", "luxury"]


def _plan(rng: random.Random, destination: dict, trip_no: int) -> TripPlan:
    days = rng.randint(2, 7)
    start = date(2025, 1, 1) + timedelta(days=rng.randint(0, 640))
    dates = DateRange(start=start, end=start + timedelta(days=days - 1))
    party = rng.randint(1, 6)
    style = rng.choice(HOTEL_STYLES)
    currency = destination["currency"]
    budget = rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 20]) * 1000

    day_plans: list[DayPlan] = []
    sight_pool = list(destination["sights"])
    for offset, day_date in enumerate(dates.dates):
        chosen = rng.sample(sight_pool, k=min(len(sight_pool), rng.randint(2, 3)))
        items: list[PlanItem] = [
            PlanItem(
                id=new_id("item"),
                type=ItemType.ATTRACTION,
                name=name,
                start="09:00" if index == 0 else ("11:00" if index == 1 else "14:30"),
                end="10:30" if index == 0 else ("12:30" if index == 1 else "16:00"),
                source_refs=[SourceRef(title=name, source="demo", url="https://example.com/demo")],
                tags=list(tags),
            )
            for index, (name, tags) in enumerate(chosen)
        ]
        items.append(
            PlanItem(
                id=new_id("item"),
                type=ItemType.MEAL,
                name=f"Lunch in {rng.choice(destination['areas'])}",
                start="13:00",
                end="14:00",
                tags=["food"],
            )
        )
        if offset % 2 == 0:
            items.append(
                PlanItem(
                    id=new_id("item"),
                    type=ItemType.MEAL,
                    name=f"Dinner in {rng.choice(destination['areas'])}",
                    start="19:00",
                    end="20:30",
                    tags=["food"],
                )
            )
        day_plans.append(
            DayPlan(
                date=day_date,
                title=f"Day {offset + 1}",
                items=sorted(items, key=lambda item: item.start),
                note="Synthetic demo day.",
            )
        )

    hotel = HotelStay(
        id=new_id("hotel"),
        name=f"{destination['name']} {style} stay",
        area=rng.choice(destination["areas"]),
        geo=GeoPoint(lat=destination["lat"], lng=destination["lng"]),
        check_in=dates.start,
        check_out=dates.end + timedelta(days=1),
        price_per_night=Money(amount=rng.randint(80, 600), currency=currency),
    )

    total = round(budget * rng.uniform(0.6, 1.1), 2)
    summary = (
        f"{days}-day {destination['name']} trip with {sum(1 for d in day_plans for i in d.items if i.type == ItemType.ATTRACTION)} "
        f"sights and a {style} stay."
    )
    return TripPlan(
        trip_id=f"{TRIP_PREFIX}{trip_no:05d}",
        session_id=f"{TRIP_PREFIX}session-{trip_no:05d}",
        destination=Destination(
            name=destination["name"],
            country_code=destination["code"],
            geo=GeoPoint(lat=destination["lat"], lng=destination["lng"]),
        ),
        origin="Hong Kong",
        dates=dates,
        party_size=party,
        budget=Money(amount=budget, currency=currency),
        summary=summary,
        days=day_plans,
        hotels=[hotel],
        cost=CostBreakdown(
            currency=currency,
            total=total,
            budget=budget,
            remaining=round(budget - total, 2),
            status="within" if total <= budget else "over",
        ),
    )


def _saved_trip(rng: random.Random, plan: TripPlan) -> SavedTrip:
    stars = rng.randint(3, 5)
    text = " ".join(
        [
            plan.destination.name,
            plan.summary,
            " ".join(tag for day in plan.days for item in day.items for tag in item.tags),
            f"{len(plan.days)} days",
            f"{plan.party_size} travellers",
        ]
    )
    created = datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 540), hours=rng.randint(0, 23))
    return SavedTrip(
        trip_id=plan.trip_id,
        destination=plan.destination.name,
        rating=stars / 5.0,
        stars=stars,
        summary=plan.summary,
        embedding=embed(text),
        plan=plan,
        created_at=created,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic kept journeys and preference profiles.")
    parser.add_argument("--trips", type=int, default=800)
    parser.add_argument("--profiles", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--reset", action="store_true", help="remove synthetic rows and exit")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    store = SQLiteStore(settings.database_path)

    if args.reset:
        trips = store.delete_saved_trips_prefix(TRIP_PREFIX)
        profiles = store.delete_preferences_prefix(PROFILE_PREFIX)
        print(f"removed {trips} synthetic journeys and {profiles} synthetic profiles")
        return

    rng = random.Random(args.seed)
    for index in range(args.trips):
        destination = rng.choice(DESTINATIONS)
        plan = _plan(rng, destination, index)
        store.save_saved_trip(_saved_trip(rng, plan))
        if (index + 1) % 200 == 0:
            print(f"  {index + 1}/{args.trips} journeys")

    for index in range(args.profiles):
        store.upsert_preferences(
            PreferenceProfile(
                client_token=f"{PROFILE_PREFIX}{index:04d}",
                preferences=rng.sample(TRAVELLER_TAGS, k=rng.randint(2, 5)),
                updated_at=utcnow(),
            )
        )

    counts = {}
    for trip in store.list_saved_trips(10000):
        if trip.trip_id.startswith(TRIP_PREFIX):
            counts[trip.destination] = counts.get(trip.destination, 0) + 1
    print(f"seeded {args.trips} journeys and {args.profiles} profiles")
    for name, count in sorted(counts.items(), key=lambda row: -row[1]):
        print(f"  {name:12s} {count}")


if __name__ == "__main__":
    main()
