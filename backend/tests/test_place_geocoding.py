from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.config import settings
from app.schemas.trip import GeocodeRequest
from app.services.place_geocoding import _cache, geocode_places


@pytest.mark.asyncio
async def test_ors_geocoding_rejects_same_country_wrong_region():
    _cache.clear()
    response = Mock()
    response.json.return_value = {"features": [
        {"properties": {"country": "India", "region": "Gujarat", "label": "Panaji, Gujarat"},
         "geometry": {"coordinates": [72.5, 23.0]}},
        {"properties": {"country": "India", "region": "Goa", "label": "Panaji, Goa, India"},
         "geometry": {"coordinates": [73.8278, 15.4909]}},
    ]}
    request = GeocodeRequest(places=[{"id": "stop-2", "name": "Panaji"}])
    with patch.object(settings, "ORS_API_KEY", "test-key"), patch(
        "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response,
    ) as get:
        result = await geocode_places(request, "Goa, India")
    assert result["places"] == [{"id": "stop-2", "lat": 15.4909, "lon": 73.8278}]
    assert get.await_args.kwargs["headers"]["Authorization"] == "test-key"
