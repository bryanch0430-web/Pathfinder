"""Google Elevation API: a small climb estimate for a day's walking order."""

from __future__ import annotations

from app.config import Settings
from app.domain.errors import ProviderNoData
from app.domain.models import GeoPoint
from app.infrastructure.http import request_json


class GoogleElevation:
    name = "google-elevation"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._key = settings.google_places_api_key or settings.google_maps_api_key
        self.available = bool(self._key and settings.enable_elevation)

    async def gain(self, points: list[GeoPoint]) -> float | None:
        """Total positive ascent in metres along the given order (max 10 points)."""
        if not self.available:
            raise ProviderNoData("Google Elevation key is not configured", source=self.name)
        sampled = points[:10]
        if len(sampled) < 2:
            return None
        data = await request_json(
            "GET",
            "https://maps.googleapis.com/maps/api/elevation/json",
            source=self.name,
            params={
                "locations": "|".join(f"{point.lat},{point.lng}" for point in sampled),
                "key": self._key,
            },
            timeout=self.settings.http_timeout_seconds,
            retries=1,
            user_agent=self.settings.user_agent,
        )
        if str(data.get("status") or "") != "OK":
            raise ProviderNoData(
                f"elevation lookup failed: {data.get('status') or 'unknown'}",
                source=self.name,
            )
        rows = data.get("results") or []
        heights = [float(row.get("elevation") or 0.0) for row in rows]
        if len(heights) < 2:
            return None
        ascent = sum(max(0.0, heights[index] - heights[index - 1]) for index in range(1, len(heights)))
        return round(ascent, 1)
