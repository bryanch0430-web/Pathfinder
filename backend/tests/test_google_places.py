from unittest.mock import AsyncMock, Mock, patch
from tempfile import TemporaryDirectory
from pathlib import Path

import pytest

from app.core.config import settings
from app.schemas.trip import NearbyPlacesRequest
from app.services.google_places import (
    MAX_GOOGLE_NEARBY_CALLS_PER_TRIP_PER_DAY,
    _nearby_cache,
    get_nearby_places,
)


@pytest.mark.asyncio
async def test_nearby_search_is_cached_and_capped_per_trip():
    _nearby_cache.clear()
    response = Mock()
    response.json.return_value = {"places": [{
        "id": "place-1", "displayName": {"text": "Museum"},
        "location": {"latitude": 35.1, "longitude": 139.1},
    }]}
    with TemporaryDirectory(dir=Path(__file__).parent) as temp_dir:
        with patch("app.services.google_places.BUDGET_DB_PATH", Path(temp_dir) / "budget.sqlite3"), patch.object(settings, "GOOGLE_MAPS_API_KEY", "demo-key"), patch(
            "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response,
        ) as post:
            first = await get_nearby_places(NearbyPlacesRequest(lat=35, lon=139), "trip-demo")
            cached = await get_nearby_places(NearbyPlacesRequest(lat=35, lon=139), "trip-demo")
            assert first == cached
            assert post.await_count == 1
            assert "priceLevel" not in post.await_args.kwargs["headers"]["X-Goog-FieldMask"]
            assert post.await_args.kwargs["json"]["rankPreference"] == "POPULARITY"
            for index in range(1, MAX_GOOGLE_NEARBY_CALLS_PER_TRIP_PER_DAY):
                await get_nearby_places(NearbyPlacesRequest(lat=35 + index, lon=139), "trip-demo")
            limited = await get_nearby_places(NearbyPlacesRequest(lat=40, lon=139), "trip-demo")
            assert limited == {"provider": "limit_reached", "places": []}
            assert post.await_count == MAX_GOOGLE_NEARBY_CALLS_PER_TRIP_PER_DAY
            _nearby_cache.clear()  # Simulate a restarted worker with the same ledger.
            still_limited = await get_nearby_places(NearbyPlacesRequest(lat=40, lon=139), "trip-demo")
            assert still_limited["provider"] == "limit_reached"
