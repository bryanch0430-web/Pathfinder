"""AMap (高德地图) maps provider — PROVISIONAL (team decision 5: vendors are provisional).

Fallback in the proposal's maps chain ("maps with Google and AMap as fallback").
"""

from __future__ import annotations

from pydantic import SecretStr

from backend.schemas.tools import GeocodeRequest, GeocodeResult


class AMapProvider:
    name = "amap"

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    async def geocode(self, request: GeocodeRequest) -> GeocodeResult:
        # TODO(provisional): AMap Web Service geocoding API (settings.amap_api_key); AMap returns
        # GCJ-02 coordinates in mainland China, convert to WGS-84 before building GeoPoint.
        raise NotImplementedError("TODO(provisional): AMap geocoding is not implemented")
