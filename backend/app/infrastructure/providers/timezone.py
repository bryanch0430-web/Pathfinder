"""Google Time Zone API: fill the destination timezone when the geocoder has none."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from app.config import Settings
from app.domain.errors import ProviderNoData
from app.domain.models import GeoPoint
from app.infrastructure.http import request_json

_CACHE: dict[tuple[float, float], str] = {}


class GoogleTimeZone:
    name = "google-timezone"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._key = settings.google_places_api_key or settings.google_maps_api_key
        self.available = bool(self._key and settings.enable_timezone)

    async def resolve(self, geo: GeoPoint) -> str:
        if not self.available:
            raise ProviderNoData("Google Time Zone key is not configured", source=self.name)
        cache_key = (round(geo.lat, 3), round(geo.lng, 3))
        cached = _CACHE.get(cache_key)
        if cached:
            return cached
        data = await request_json(
            "GET",
            "https://maps.googleapis.com/maps/api/timezone/json",
            source=self.name,
            params={
                "location": f"{geo.lat},{geo.lng}",
                "timestamp": int(datetime.now(dt_timezone.utc).timestamp()),
                "key": self._key,
            },
            timeout=self.settings.http_timeout_seconds,
            retries=1,
            user_agent=self.settings.user_agent,
        )
        if str(data.get("status") or "") != "OK":
            raise ProviderNoData(
                f"time zone lookup failed: {data.get('status') or 'unknown'}",
                source=self.name,
            )
        zone = str(data.get("timeZoneId") or "").strip()
        if not zone:
            raise ProviderNoData("time zone lookup returned no zone", source=self.name)
        _CACHE[cache_key] = zone
        return zone
