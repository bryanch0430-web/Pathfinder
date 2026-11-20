from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.config import settings
from app.schemas.trip import RouteMetricsRequest
from app.services.route_metrics import get_route_metrics, _cache


@pytest.mark.asyncio
async def test_ors_result_is_mapped_to_distance_duration_and_shape():
    _cache.clear()
    response = Mock()
    response.json.return_value = {"features": [{
        "properties": {"summary": {"distance": 12345, "duration": 1800}},
        "geometry": {"coordinates": [[139.69, 35.68], [139.7, 35.69]]},
    }]}
    request = RouteMetricsRequest(legs=[{
        "id": "leg-1", "from_lat": 35.68, "from_lon": 139.69,
        "to_lat": 35.69, "to_lon": 139.7,
    }])
    with patch.object(settings, "ORS_API_KEY", "test-key"), patch(
        "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response,
    ) as post:
        result = await get_route_metrics(request)
    assert result["legs"][0]["distance_km"] == 12.3
    assert result["legs"][0]["duration_minutes"] == 30
    assert result["legs"][0]["geometry"][0] == [139.69, 35.68]
    assert post.await_args.kwargs["headers"]["Authorization"] == "test-key"
