"""Google Maps / Google Places providers — PROVISIONAL (team decision 5: vendors are provisional).

Typed shells only, so the factory and the configuration have a named place for the real
integration. Every method raises NotImplementedError until the vendor is confirmed.
"""

from __future__ import annotations

from pydantic import SecretStr

from backend.schemas.tools import GeocodeRequest, GeocodeResult, PlaceRecord, PlacesSearchRequest


class GoogleMapsProvider:
    """Primary maps provider in the proposal's chain ("maps with Google and AMap as fallback")."""

    name = "google-maps"

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    async def geocode(self, request: GeocodeRequest) -> GeocodeResult:
        # TODO(provisional): Google Maps Geocoding API (settings.google_maps_api_key); map HTTP
        # 429 -> ProviderRateLimited, 5xx -> ProviderServerError, 400 -> ProviderBadRequest,
        # auth/quota errors -> ProviderUnavailable.
        raise NotImplementedError("TODO(provisional): Google Maps geocoding is not implemented")


class GooglePlacesProvider:
    """Places (attractions/hotels) via Google Places."""

    name = "google-places"

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    async def search(self, request: PlacesSearchRequest) -> list[PlaceRecord]:
        # TODO(provisional): Google Places API (Text Search / Nearby Search) for attractions and
        # lodging; map price_level / opening_hours into PlaceRecord.
        raise NotImplementedError("TODO(provisional): Google Places search is not implemented")
