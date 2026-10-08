"""Static, deterministic dataset behind the mock providers.

Why static data: team decision 5 says only mock implementations are concrete, so the whole
system (and the evaluation harness) runs offline with no API key. Kyoto, Tokyo, Osaka and Hong
Kong use hand-written records with realistic coordinates and prices; any other destination gets
generated records seeded from the city name via hashlib (never Python's salted `hash()`), so the
same input yields the same data in every process.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeVar


@dataclass(frozen=True, slots=True)
class Attraction:
    name: str
    category: str
    lat: float
    lng: float
    indoor: bool
    rating: float
    price: float  # per person, in the city's currency (0 = free)
    opening_hours: str
    needs_reservation: bool = False
    lead_time_days: int = 0


@dataclass(frozen=True, slots=True)
class Hotel:
    name: str
    style: str  # one of HOTEL_STYLES
    lat: float
    lng: float
    rating: float
    nightly_price: float  # per room per night, in the city's currency


@dataclass(frozen=True, slots=True)
class City:
    key: str  # slug, e.g. "hong-kong"
    name: str  # display name, e.g. "Hong Kong"
    country: str
    currency: str
    lat: float
    lng: float
    utc_offset_h: int
    region: str  # rail network region; trains only run inside one region
    monthly_temps: tuple[tuple[float, float], ...]  # 12 x (min_c, max_c)
    monthly_rain: tuple[float, ...]  # 12 x base precipitation chance
    attractions: tuple[Attraction, ...]
    hotels: tuple[Hotel, ...]
    generated: bool = False


HOTEL_STYLES: tuple[str, ...] = ("budget", "business", "boutique", "luxury", "ryokan")

# USD value of one unit of each currency (fixed mock rates; only used to compare and convert).
USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "JPY": 1 / 150,
    "HKD": 1 / 7.8,
    "EUR": 1.08,
    "GBP": 1.27,
    "CNY": 1 / 7.2,
    "TWD": 1 / 32,
    "SGD": 0.74,
    "KRW": 1 / 1370,
    "AUD": 0.66,
}

_JAPAN_RAIN = (0.25, 0.3, 0.35, 0.35, 0.35, 0.55, 0.5, 0.4, 0.45, 0.35, 0.3, 0.25)

KYOTO = City(
    key="kyoto",
    name="Kyoto",
    country="Japan",
    currency="JPY",
    lat=35.0116,
    lng=135.7681,
    utc_offset_h=9,
    region="japan",
    monthly_temps=(
        (1, 9), (1, 10), (4, 14), (9, 20), (14, 25), (19, 28),
        (23, 32), (24, 34), (20, 29), (14, 23), (8, 17), (3, 12),
    ),
    monthly_rain=_JAPAN_RAIN,
    attractions=(
        Attraction("Fushimi Inari Taisha", "shrine", 34.9671, 135.7727, False, 4.7, 0, "Open 24 hours"),
        Attraction("Kiyomizu-dera", "temple", 34.9949, 135.7850, False, 4.6, 500, "06:00-18:00"),
        Attraction("Kinkaku-ji (Golden Pavilion)", "temple", 35.0394, 135.7292, False, 4.6, 500, "09:00-17:00"),
        Attraction("Ginkaku-ji (Silver Pavilion)", "temple", 35.0270, 135.7982, False, 4.4, 500, "08:30-17:00"),
        Attraction("Arashiyama Bamboo Grove", "park", 35.0170, 135.6713, False, 4.4, 0, "Open 24 hours"),
        Attraction("Nishiki Market", "market", 35.0050, 135.7649, True, 4.3, 0, "10:00-18:00"),
        Attraction("Kyoto National Museum", "museum", 34.9899, 135.7732, True, 4.4, 700, "09:30-17:00 (closed Mondays)"),
        Attraction("Gion District", "district", 35.0037, 135.7788, False, 4.5, 0, "Open 24 hours"),
        Attraction("Nijo Castle", "castle", 35.0142, 135.7481, False, 4.5, 1300, "08:45-17:00"),
        Attraction("Kyoto Railway Museum", "museum", 34.9871, 135.7425, True, 4.6, 1500, "10:00-17:00 (closed Wednesdays)"),
        Attraction("Kyoto Aquarium", "aquarium", 34.9877, 135.7468, True, 4.2, 2400, "10:00-18:00"),
        Attraction("Philosopher's Path", "park", 35.0236, 135.7944, False, 4.4, 0, "Open 24 hours"),
        Attraction("Kyoto Tower", "viewpoint", 34.9875, 135.7593, True, 4.0, 900, "10:00-21:00"),
        Attraction(
            "Saihoji (Moss Temple)", "temple", 34.9917, 135.6838, False, 4.6, 4000,
            "By advance reservation only", needs_reservation=True, lead_time_days=30,
        ),
    ),
    hotels=(
        Hotel("Piece Hostel Sanjo", "budget", 35.0089, 135.7735, 4.5, 3800),
        Hotel("K's House Kyoto", "budget", 34.9877, 135.7637, 4.4, 4500),
        Hotel("Hotel Gracery Kyoto Sanjo", "business", 35.0087, 135.7695, 4.2, 14000),
        Hotel("Hotel Granvia Kyoto", "business", 34.9854, 135.7588, 4.4, 22000),
        Hotel("Hotel Kanra Kyoto", "boutique", 34.9948, 135.7598, 4.6, 32000),
        Hotel("Hiiragiya Ryokan", "ryokan", 35.0098, 135.7651, 4.7, 70000),
        Hotel("The Ritz-Carlton, Kyoto", "luxury", 35.0124, 135.7720, 4.8, 120000),
    ),
)

TOKYO = City(
    key="tokyo",
    name="Tokyo",
    country="Japan",
    currency="JPY",
    lat=35.6812,
    lng=139.7671,
    utc_offset_h=9,
    region="japan",
    monthly_temps=(
        (1, 10), (2, 11), (5, 14), (10, 19), (15, 23), (19, 26),
        (23, 30), (24, 31), (21, 27), (15, 22), (9, 17), (4, 12),
    ),
    monthly_rain=_JAPAN_RAIN,
    attractions=(
        Attraction("Senso-ji", "temple", 35.7148, 139.7967, False, 4.5, 0, "06:00-17:00"),
        Attraction("Meiji Jingu", "shrine", 35.6764, 139.6993, False, 4.6, 0, "Sunrise to sunset"),
        Attraction("Tokyo Skytree", "viewpoint", 35.7101, 139.8107, True, 4.5, 2100, "10:00-22:00"),
        Attraction("Shibuya Crossing", "district", 35.6595, 139.7005, False, 4.4, 0, "Open 24 hours"),
        Attraction("Tsukiji Outer Market", "market", 35.6655, 139.7707, False, 4.3, 0, "05:00-14:00"),
        Attraction("Tokyo National Museum", "museum", 35.7188, 139.7765, True, 4.5, 1000, "09:30-17:00 (closed Mondays)"),
        Attraction("Ueno Park", "park", 35.7156, 139.7745, False, 4.4, 0, "05:00-23:00"),
        Attraction("Shinjuku Gyoen National Garden", "park", 35.6852, 139.7100, False, 4.6, 500, "09:00-17:30 (closed Mondays)"),
        Attraction(
            "teamLab Planets TOKYO", "museum", 35.6492, 139.7898, True, 4.6, 3800,
            "09:00-22:00 (timed entry)", needs_reservation=True, lead_time_days=7,
        ),
        Attraction(
            "Ghibli Museum", "museum", 35.6962, 139.5704, True, 4.5, 1000,
            "10:00-18:00 (closed Tuesdays)", needs_reservation=True, lead_time_days=30,
        ),
        Attraction("Tokyo Tower", "viewpoint", 35.6586, 139.7454, True, 4.4, 1200, "09:00-22:30"),
        Attraction("Akihabara Electric Town", "district", 35.6984, 139.7731, False, 4.3, 0, "10:00-21:00"),
        Attraction("Tokyo Disneyland", "theme_park", 35.6329, 139.8804, False, 4.6, 8400, "09:00-21:00"),
        Attraction("Sumida Aquarium", "aquarium", 35.7100, 139.8096, True, 4.3, 2500, "10:00-20:00"),
    ),
    hotels=(
        Hotel("Khaosan Tokyo Kabuki", "budget", 35.7105, 139.7953, 4.2, 4000),
        Hotel("Dormy Inn Akihabara", "business", 35.6985, 139.7740, 4.3, 13000),
        Hotel("Hotel Gracery Shinjuku", "business", 35.6950, 139.7020, 4.3, 18000),
        Hotel("Hotel Niwa Tokyo", "boutique", 35.7020, 139.7570, 4.4, 26000),
        Hotel("Hoshinoya Tokyo", "ryokan", 35.6866, 139.7650, 4.7, 95000),
        Hotel("Park Hyatt Tokyo", "luxury", 35.6856, 139.6907, 4.7, 110000),
    ),
)

OSAKA = City(
    key="osaka",
    name="Osaka",
    country="Japan",
    currency="JPY",
    lat=34.6937,
    lng=135.5023,
    utc_offset_h=9,
    region="japan",
    monthly_temps=(
        (3, 10), (3, 10), (6, 14), (11, 20), (16, 25), (20, 28),
        (24, 32), (25, 34), (22, 29), (16, 24), (10, 18), (5, 12),
    ),
    monthly_rain=_JAPAN_RAIN,
    attractions=(
        Attraction("Osaka Castle", "castle", 34.6873, 135.5262, False, 4.5, 600, "09:00-17:00"),
        Attraction("Dotonbori", "district", 34.6687, 135.5013, False, 4.5, 0, "Open 24 hours"),
        Attraction("Kuromon Ichiba Market", "market", 34.6656, 135.5064, True, 4.1, 0, "09:00-18:00"),
        Attraction("Shitenno-ji", "temple", 34.6536, 135.5164, False, 4.3, 300, "08:30-16:30"),
        Attraction("Sumiyoshi Taisha", "shrine", 34.6127, 135.4930, False, 4.5, 0, "06:00-17:00"),
        Attraction("Osaka Aquarium Kaiyukan", "aquarium", 34.6545, 135.4290, True, 4.6, 2700, "10:00-20:00"),
        Attraction("Universal Studios Japan", "theme_park", 34.6654, 135.4323, False, 4.5, 8600, "09:00-21:00"),
        Attraction("Umeda Sky Building Floating Garden Observatory", "viewpoint", 34.7053, 135.4896, True, 4.4, 1500, "09:30-22:30"),
        Attraction("Shinsekai", "district", 34.6525, 135.5063, False, 4.1, 0, "Open 24 hours"),
        Attraction("National Museum of Art, Osaka", "museum", 34.6918, 135.4918, True, 4.2, 430, "10:00-17:00 (closed Mondays)"),
        Attraction("Osaka Museum of History", "museum", 34.6823, 135.5204, True, 4.4, 600, "09:30-17:00 (closed Tuesdays)"),
        Attraction("Expo '70 Commemorative Park", "park", 34.8094, 135.5323, False, 4.4, 260, "09:30-17:00 (closed Wednesdays)"),
    ),
    hotels=(
        Hotel("Hostel 64 Osaka", "budget", 34.6780, 135.4930, 4.4, 4200),
        Hotel("Kaneyoshi Ryokan", "ryokan", 34.6683, 135.5025, 4.0, 16000),
        Hotel("Hotel Monterey Grasmere Osaka", "business", 34.6672, 135.4985, 4.3, 15000),
        Hotel("Hotel Nikko Osaka", "business", 34.6740, 135.5004, 4.3, 19000),
        Hotel("Cross Hotel Osaka", "boutique", 34.6698, 135.5012, 4.3, 22000),
        Hotel("The St. Regis Osaka", "luxury", 34.6826, 135.5000, 4.6, 75000),
        Hotel("Conrad Osaka", "luxury", 34.6937, 135.4930, 4.6, 85000),
    ),
)

HONG_KONG = City(
    key="hong-kong",
    name="Hong Kong",
    country="Hong Kong SAR",
    currency="HKD",
    lat=22.2819,
    lng=114.1582,
    utc_offset_h=8,
    region="greater-china",
    monthly_temps=(
        (15, 19), (15, 20), (18, 22), (21, 26), (24, 29), (26, 31),
        (27, 32), (27, 32), (26, 31), (24, 28), (20, 25), (16, 21),
    ),
    monthly_rain=(0.15, 0.2, 0.3, 0.35, 0.5, 0.6, 0.55, 0.55, 0.45, 0.25, 0.15, 0.12),
    # Indoor and outdoor interleaved so a default-limit places search returns a mix (the
    # typhoon/rainstorm scenarios need indoor alternatives).
    attractions=(
        Attraction("Victoria Peak", "viewpoint", 22.2759, 114.1455, False, 4.6, 88, "Open 24 hours; Peak Tram 07:30-23:00"),
        Attraction("Hong Kong Museum of Art", "museum", 22.2934, 114.1719, True, 4.4, 20, "10:00-18:00 (closed Thursdays)"),
        Attraction("Tian Tan Buddha", "temple", 22.2540, 113.9050, False, 4.6, 0, "10:00-17:30"),
        Attraction("M+", "museum", 22.3015, 114.1597, True, 4.5, 140, "10:00-18:00 (closed Mondays)"),
        Attraction("Star Ferry Pier (Tsim Sha Tsui)", "landmark", 22.2936, 114.1683, False, 4.6, 5, "06:30-23:30"),
        Attraction(
            "Hong Kong Palace Museum", "museum", 22.3016, 114.1567, True, 4.5, 60,
            "10:00-18:00 (closed Tuesdays; timed entry)", needs_reservation=True, lead_time_days=2,
        ),
        Attraction("Temple Street Night Market", "market", 22.3060, 114.1700, False, 4.0, 0, "18:00-23:00"),
        Attraction("Hong Kong Science Museum", "museum", 22.3011, 114.1774, True, 4.3, 20, "10:00-19:00 (closed Thursdays)"),
        Attraction("Dragon's Back Trail", "trail", 22.2470, 114.2405, False, 4.6, 0, "Open 24 hours"),
        Attraction("Man Mo Temple", "temple", 22.2840, 114.1503, True, 4.4, 0, "08:00-18:00"),
        Attraction("Ngong Ping 360", "viewpoint", 22.2896, 113.9418, False, 4.4, 270, "10:00-18:00"),
        Attraction("Hong Kong Space Museum", "museum", 22.2942, 114.1719, True, 4.2, 10, "10:00-21:00 (closed Tuesdays)"),
        Attraction("Ocean Park", "theme_park", 22.2467, 114.1757, False, 4.4, 498, "10:00-18:00"),
        Attraction("Avenue of Stars", "viewpoint", 22.2930, 114.1747, False, 4.3, 0, "Open 24 hours"),
        Attraction("Chi Lin Nunnery", "temple", 22.3404, 114.2049, False, 4.6, 0, "09:00-17:00"),
    ),
    hotels=(
        Hotel("Yesinn @YMT", "budget", 22.3110, 114.1710, 4.2, 450),
        Hotel("Ibis Hong Kong Central and Sheung Wan", "business", 22.2873, 114.1440, 4.0, 900),
        Hotel("Dorsett Wanchai", "business", 22.2768, 114.1757, 4.1, 1100),
        Hotel("Tuve", "boutique", 22.2836, 114.1925, 4.3, 1300),
        Hotel("Hotel Indigo Hong Kong Island", "boutique", 22.2770, 114.1710, 4.3, 1500),
        Hotel("Mandarin Oriental, Hong Kong", "luxury", 22.2817, 114.1594, 4.7, 4800),
        Hotel("The Peninsula Hong Kong", "luxury", 22.2950, 114.1719, 4.7, 5200),
    ),
)

KNOWN_CITIES: tuple[City, ...] = (KYOTO, TOKYO, OSAKA, HONG_KONG)

# Normalised alias -> city. Matching is case-insensitive, accent-insensitive and tolerant of
# suffixes like ", Japan" (see `find_city`).
_ALIASES: dict[str, City] = {
    "kyoto": KYOTO,
    "tokyo": TOKYO,
    "osaka": OSAKA,
    "hong kong": HONG_KONG,
    "hongkong": HONG_KONG,
    "hk": HONG_KONG,
    "hksar": HONG_KONG,
}
_CJK_ALIASES: dict[str, City] = {"京都": KYOTO, "東京": TOKYO, "东京": TOKYO, "大阪": OSAKA, "香港": HONG_KONG}


# ------------------------------------------------------------------------------------------------
# Text helpers
# ------------------------------------------------------------------------------------------------


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize(text: str) -> str:
    """Lower-case, accent-free, punctuation collapsed to single spaces."""
    folded = _ascii_fold(text.replace("+", " plus ").replace("'", "").replace("’", ""))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


def slugify(text: str) -> str:
    return normalize(text).replace(" ", "-") or "x"


def name_keys(name: str) -> frozenset[str]:
    """Slugs a place may be referred to by: full name, name without its parenthetical, and the
    parenthetical itself ("Kinkaku-ji (Golden Pavilion)" -> kinkaku-ji, golden-pavilion, ...)."""
    keys = {slugify(name)}
    match = re.match(r"^(.*?)\s*\((.*)\)\s*$", name)
    if match:
        keys.add(slugify(match.group(1)))
        keys.add(slugify(match.group(2)))
    return frozenset(k for k in keys if k != "x")


def stable_unit(*parts: str) -> float:
    """Deterministic float in [0, 1) from the given parts (sha256, process-independent)."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def stable_rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# ------------------------------------------------------------------------------------------------
# City lookup
# ------------------------------------------------------------------------------------------------


def find_city(text: str) -> City | None:
    """Tolerant lookup of a known city: "kyoto", "Kyoto, Japan", "HK", "Hong Kong Island"."""
    for cjk, city in _CJK_ALIASES.items():
        if cjk in text:
            return city
    norm = normalize(text)
    if norm in _ALIASES:
        return _ALIASES[norm]
    first = normalize(text.split(",")[0])
    if first in _ALIASES:
        return _ALIASES[first]
    padded = f" {norm} "
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if f" {alias} " in padded:
            return _ALIASES[alias]
    return None


def city_key(text: str) -> str:
    """Stable key for any destination text: the known city's key, else a slug of the first
    comma-separated part ("Paris, France" -> "paris")."""
    known = find_city(text)
    if known is not None:
        return known.key
    return slugify(text.split(",")[0])


def is_city_name(city: City, text: str) -> bool:
    """True when `text` names the city itself ("Kyoto", "HK", "Kyoto, Japan"), not a place in
    it ("Kyoto Tower")."""
    if city.generated:
        return city_key(text) == city.key
    first = normalize(text.split(",")[0])
    return (
        first == normalize(city.name)
        or _ALIASES.get(first) is city
        or _CJK_ALIASES.get(text.strip()) is city
    )


def resolve_city(text: str) -> City:
    """Known city, or deterministic generated data for any other destination."""
    known = find_city(text)
    if known is not None:
        return known
    return _generated_city(city_key(text), text.split(",")[0].strip())


_GEN_ATTRACTIONS: tuple[tuple[str, str, bool, float, str], ...] = (
    # (suffix, category, indoor, base price USD, opening hours)
    ("Old Town", "district", False, 0, "Open 24 hours"),
    ("City Museum", "museum", True, 12, "09:00-17:00 (closed Mondays)"),
    ("Central Market", "market", True, 0, "08:00-18:00"),
    ("Botanical Garden", "park", False, 8, "08:00-18:00"),
    ("Old Cathedral", "landmark", True, 0, "09:00-18:00"),
    ("Art Gallery", "museum", True, 15, "10:00-18:00 (closed Tuesdays)"),
    ("Riverside Promenade", "park", False, 0, "Open 24 hours"),
    ("Observation Tower", "viewpoint", True, 20, "10:00-22:00"),
    ("Science Centre", "museum", True, 18, "09:30-17:30"),
    ("Aquarium", "aquarium", True, 25, "10:00-18:00"),
    ("Night Market", "market", False, 0, "18:00-23:00"),
    ("Hilltop Lookout", "viewpoint", False, 0, "Open 24 hours"),
)

_GEN_HOTELS: tuple[tuple[str, str, float, float], ...] = (
    # (suffix, style, min USD, max USD)
    ("Central Hostel", "budget", 25, 40),
    ("Budget Inn", "budget", 45, 70),
    ("Business Hotel", "business", 90, 140),
    ("Garden Hotel", "business", 110, 160),
    ("Boutique House", "boutique", 150, 220),
    ("Grand Hotel", "luxury", 280, 450),
)


@lru_cache(maxsize=256)
def _generated_city(key: str, raw_name: str) -> City:
    rng = stable_rng("city", key)
    display = raw_name.strip().title() or key.title()
    lat = round(rng.uniform(-45.0, 60.0), 4)
    lng = round(rng.uniform(-170.0, 170.0), 4)

    def near(spread_km: float) -> tuple[float, float]:
        dist = rng.uniform(0.3, spread_km)
        theta = rng.uniform(0, 2 * math.pi)
        return offset_point(lat, lng, dist, theta)

    reservation_index = rng.randrange(len(_GEN_ATTRACTIONS))
    attractions = []
    for i, (suffix, category, indoor, base_usd, hours) in enumerate(_GEN_ATTRACTIONS):
        a_lat, a_lng = near(5.0)
        attractions.append(
            Attraction(
                name=f"{display} {suffix}",
                category=category,
                lat=a_lat,
                lng=a_lng,
                indoor=indoor,
                rating=round(rng.uniform(3.8, 4.8), 1),
                price=float(round(base_usd * rng.uniform(0.8, 1.3))) if base_usd else 0.0,
                opening_hours=hours,
                needs_reservation=i == reservation_index,
                lead_time_days=rng.randint(1, 14) if i == reservation_index else 0,
            )
        )
    hotels = []
    for suffix, style, lo, hi in _GEN_HOTELS:
        h_lat, h_lng = near(3.0)
        hotels.append(
            Hotel(
                name=f"{display} {suffix}",
                style=style,
                lat=h_lat,
                lng=h_lng,
                rating=round(rng.uniform(3.6, 4.7), 1),
                nightly_price=float(round(rng.uniform(lo, hi))),
            )
        )
    temps = []
    for month in range(1, 13):
        mean = 15 - 9 * math.cos(2 * math.pi * (month - 1) / 12)
        temps.append((round(mean - 4, 1), round(mean + 4, 1)))
    return City(
        key=key,
        name=display,
        country="",
        currency="USD",
        lat=lat,
        lng=lng,
        utc_offset_h=max(-12, min(14, round(lng / 15))),
        region=f"generated:{key}",
        monthly_temps=tuple(temps),
        monthly_rain=tuple(0.35 for _ in range(12)),
        attractions=tuple(attractions),
        hotels=tuple(hotels),
        generated=True,
    )


# ------------------------------------------------------------------------------------------------
# Place lookup, geometry and money
# ------------------------------------------------------------------------------------------------


def place_id(city: City, name: str) -> str:
    return f"{city.key}-{slugify(name)}"


def find_attraction(city: City, query: str) -> Attraction | None:
    return _find_named(city.attractions, query)


def find_hotel(city: City, query: str) -> Hotel | None:
    return _find_named(city.hotels, query)


_Named = TypeVar("_Named", Attraction, Hotel)


def _find_named(items: tuple[_Named, ...], query: str) -> _Named | None:
    """Exact (alias-aware) match first; then whole-word containment either way, as long as the
    contained part is specific (2+ words or 8+ chars), so "Kinkaku-ji Temple" finds
    "Kinkaku-ji (Golden Pavilion)" but a bare "museum" matches nothing."""
    wanted = slugify(query)
    for item in items:
        if wanted in name_keys(item.name):
            return item
    for item in items:
        for key in name_keys(item.name):
            if _contains_words(key, wanted) or _contains_words(wanted, key):
                return item
    return None


def _contains_words(outer: str, inner: str) -> bool:
    specific = inner.count("-") >= 1 or len(inner) >= 8
    return specific and f"-{inner}-" in f"-{outer}-"


def matches_place(name: str, pid: str, wanted: str) -> bool:
    """True when `wanted` (a name or a place_id) designates the place."""
    key = slugify(wanted)
    return key == pid or key in name_keys(name)


def offset_point(lat: float, lng: float, dist_km: float, theta: float) -> tuple[float, float]:
    d_lat = (dist_km / 111.32) * math.cos(theta)
    d_lng = (dist_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))) * math.sin(theta)
    return round(max(-90.0, min(90.0, lat + d_lat)), 6), round(((lng + d_lng + 180) % 360) - 180, 6)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def convert(amount: float, from_ccy: str, to_ccy: str) -> float:
    """Convert with fixed mock rates; unknown currencies are treated as `to_ccy` (no-op)."""
    src, dst = USD_PER_UNIT.get(from_ccy.upper()), USD_PER_UNIT.get(to_ccy.upper())
    if src is None or dst is None or from_ccy.upper() == to_ccy.upper():
        return amount
    return amount * src / dst


def round_money(amount: float, currency: str) -> float:
    if currency.upper() in {"JPY", "KRW", "TWD"}:
        return float(round(amount, -1))
    return float(round(amount))
