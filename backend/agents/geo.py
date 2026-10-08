"""Small geometry helpers (no external service)."""

from __future__ import annotations

import math
import re
import unicodedata

from backend.schemas.common import GeoPoint

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def normalise_name(text: str) -> str:
    """Case/accent/punctuation-insensitive key for matching names across tools."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", folded.casefold()).strip()


def slug(text: str) -> str:
    return normalise_name(text).replace(" ", "-") or "item"
