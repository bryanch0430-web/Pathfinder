"""Geocoding adapters: Google when configured, Open-Meteo and Nominatim otherwise."""

from __future__ import annotations

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import GeoLocation, GeoPoint
from app.infrastructure.http import request_json

_GEOCODE_CACHE: dict[tuple[str, str], GeoLocation] = {}


class GoogleGeocoder:
    """Geocoding API: better disambiguation and country filtering than OSM names."""

    name = "google-geocoder"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._key = settings.google_places_api_key or settings.google_maps_api_key
        self.available = bool(self._key)

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        if not self.available:
            raise ProviderNoData("Google Geocoding key is not configured", source=self.name)
        cache_key = (name.strip().lower(), country_hint.strip().lower())
        cached = _GEOCODE_CACHE.get(cache_key)
        if cached:
            return cached
        params = {"address": name, "language": "en", "key": self._key}
        hint = country_hint.strip().upper()
        if len(hint) == 2 and hint.isalpha():
            params["components"] = f"country:{hint}"
        data = await request_json(
            "GET",
            "https://maps.googleapis.com/maps/api/geocode/json",
            source=self.name,
            params=params,
            timeout=self.settings.http_timeout_seconds,
            retries=1,
            user_agent=self.settings.user_agent,
        )
        if str(data.get("status") or "") != "OK":
            return None
        results = data.get("results") or []
        if not results:
            return None
        row = results[0]
        geometry = (row.get("geometry") or {}).get("location") or {}
        if geometry.get("lat") is None or geometry.get("lng") is None:
            return None
        country_code = ""
        for component in row.get("address_components") or []:
            if "country" in (component.get("types") or []):
                country_code = str(component.get("short_name") or "").upper()
                break
        location = GeoLocation(
            name=str(row.get("formatted_address") or name),
            country_code=country_code,
            timezone="",
            geo=GeoPoint(lat=float(geometry["lat"]), lng=float(geometry["lng"])),
            source=self.name,
        )
        _GEOCODE_CACHE[cache_key] = location
        return location


class OpenMeteoGeocoder:
    name = "open-meteo-geocoder"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        data = await request_json(
            "GET",
            "https://geocoding-api.open-meteo.com/v1/search",
            source=self.name,
            params={
                "name": name,
                "count": 8,
                "language": "en",
                "format": "json",
            },
            timeout=self.settings.http_timeout_seconds,
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        results = data.get("results") or []
        if not results:
            return None
        chosen = results[0]
        if country_hint:
            hint = country_hint.lower()
            for row in results:
                if hint in str(row.get("country", "")).lower() or hint == str(row.get("country_code", "")).lower():
                    chosen = row
                    break
        label = ", ".join(
            part for part in [chosen.get("name"), chosen.get("admin1"), chosen.get("country")] if part
        )
        return GeoLocation(
            name=label or name,
            country_code=str(chosen.get("country_code") or "").upper(),
            timezone=str(chosen.get("timezone") or ""),
            geo=GeoPoint(lat=float(chosen["latitude"]), lng=float(chosen["longitude"])),
            source=self.name,
        )


class NominatimGeocoder:
    name = "nominatim"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        data = await request_json(
            "GET",
            "https://nominatim.openstreetmap.org/search",
            source=self.name,
            params={"q": name, "format": "jsonv2", "limit": 3, "addressdetails": 1},
            timeout=self.settings.http_timeout_seconds,
            retries=0,
            user_agent=f"{self.settings.user_agent} (contact: student project)",
        )
        rows = data.get("data") if isinstance(data.get("data"), list) else data if isinstance(data, list) else []
        if not rows:
            return None
        row = rows[0]
        address = row.get("address") or {}
        label = row.get("display_name") or name
        return GeoLocation(
            name=str(label),
            country_code=str(address.get("country_code") or "").upper(),
            timezone="",
            geo=GeoPoint(lat=float(row["lat"]), lng=float(row["lon"])),
            source=self.name,
        )


class GeocoderChain:
    name = "geocoder-chain"

    def __init__(self, sources: list):
        self.sources = sources

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        errors: list[str] = []
        for source in self.sources:
            try:
                result = await source.geocode(name, country_hint=country_hint)
                if result:
                    return result
            except ProviderNoData:
                continue
            except ProviderError as exc:
                errors.append(f"{source.name}: {exc}")
        if errors:
            raise ProviderError(
                "geocoding failed for all sources: " + "; ".join(errors),
                source=self.name,
            )
        return None
