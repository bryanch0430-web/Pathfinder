"""What the traveller profile changes in planning. All of it is code: no prompt decides any of it.

- pace: stops a day (relaxed 2, balanced ``max_attractions_per_day``, packed 5) and the gap kept between stops.
- children: places of a family-suited type come first (a 0.15 boost on a 0..1 rating score), no stop ends after 19:00,
  and a single transfer over 45 minutes is flagged in the assessment.
- business: two stops a day, after 17:00, unless the traveller asks for daytime stops (or a packed pace).
- step-free: places the source marks accessible come first, a walk over 800 m becomes transit or a drive, and the
  places the source did not confirm are listed in the assessment.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.domain.models import TravellerProfile

#: What a pace allowed a day before the profile: the preference tags "slow pace" / "fast pace" still give these.
PACE_ATTRACTION_CAP = {"slow": 3, "balanced": 4, "fast": 5}
RELAXED_STOPS = 2
PACKED_STOPS = 5
BUSINESS_STOPS = 2
#: Minutes kept between the end of a stop and the start of the next (the transfer is part of it). A balanced day has
#: no floor: it is laid out as it always was.
PACE_MIN_GAP = {"relaxed": 45, "packed": 15}

FAMILY_BOOST = 0.15
FAMILY_TYPES = frozenset({"zoo", "aquarium", "park", "playground", "amusement_park"})
_CHILD_WORDS = ("children", "child", "kids", "science")
#: No stop of a trip with children ends after this (minutes after midnight).
CHILDREN_STOPS_END_BY = 19 * 60
#: A single transfer longer than this is flagged on a trip with children.
CHILDREN_LONG_TRANSFER_MINUTES = 45
#: A business traveller's sightseeing starts after this, unless they ask for daytime stops.
BUSINESS_FIRST_START = 17 * 60
DAYTIME_TAG = "daytime stops"


def stops_cap(settings: Settings, pace: str, business: bool = False) -> int:
    """The most sightseeing stops a day for a pace. ``relaxed`` and ``packed`` are the profile's paces; ``slow`` and ``fast``
    are the older preference-tag ones and keep what they gave. A business trip keeps two a day unless the pace is packed."""
    if pace == "relaxed":
        cap = RELAXED_STOPS
    elif pace == "packed":
        cap = max(PACKED_STOPS, settings.max_attractions_per_day)
    else:
        cap = min(settings.max_attractions_per_day, PACE_ATTRACTION_CAP.get(pace, 4))
    if business and pace != "packed":
        cap = min(cap, BUSINESS_STOPS)
    return cap


def profile_of(context: Any) -> TravellerProfile:
    """The profile of a context (a stand-in without one has the default)."""
    profile = getattr(context, "profile", None)
    return profile if isinstance(profile, TravellerProfile) else TravellerProfile()


def is_business(context: Any) -> bool:
    return profile_of(context).purpose == "business"


def business_first_start(context: Any) -> int | None:
    """When a business trip's stops may start (17:00), or None for another trip or when daytime stops were asked for."""
    if not is_business(context):
        return None
    tags = {str(item).strip().lower() for item in getattr(context, "preferences", [])}
    return None if DAYTIME_TAG in tags else BUSINESS_FIRST_START


def family_suited(row: dict) -> bool:
    """A place whose source types say it suits children: a zoo, aquarium, park, playground or amusement park, or a museum
    that says it is for children or science."""
    tags = {str(tag).strip().lower() for tag in row.get("tags") or []}
    if tags & FAMILY_TYPES:
        return True
    if "museum" in tags:
        text = " ".join(
            [str(row.get("name") or ""), str(row.get("description") or ""), *tags]
        ).lower()
        return any(word in text for word in _CHILD_WORDS)
    return False


def _rating_score(row: dict) -> float:
    rating = row.get("rating")
    return float(rating) / 5.0 if isinstance(rating, (int, float)) else 0.5


def _step_tier(row: dict) -> int:
    """0 for a place the source marks step-free, 1 when it did not say, 2 when it says there is no step-free entrance."""
    value = row.get("step_free")
    return 0 if value is True else 2 if value is False else 1


def profile_order(rows: list[dict], profile: TravellerProfile) -> list[dict]:
    """The candidate places in the order the profile prefers, before any are chosen. Nothing is removed, and with
    no profile effect the list comes back as it was."""
    if not profile.with_children and not profile.step_free:
        return list(rows)

    def key(row: dict) -> tuple[int, float]:
        tier = _step_tier(row) if profile.step_free else 0
        score = 0.0
        if profile.with_children:
            score = _rating_score(row) + (FAMILY_BOOST if family_suited(row) else 0.0)
        return tier, -score

    return sorted(rows, key=key)  # stable: ties keep their order
