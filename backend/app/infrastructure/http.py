"""Small, shared HTTP helper with explicit failure classification."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def classify_status(status: int, source: str, message: str = "") -> ProviderError:
    detail = message or f"{source} returned HTTP {status}"
    if status in {401, 403}:
        return ProviderError(detail, kind=FailureKind.AUTH, source=source, retryable=False)
    if status == 429:
        return ProviderError(detail, kind=FailureKind.RATE_LIMIT, source=source)
    if status in {400, 422}:
        return ProviderError(detail, kind=FailureKind.INVALID_REQUEST, source=source, retryable=False)
    if status == 404:
        return ProviderNoData(detail, source=source)
    if status >= 500:
        return ProviderError(detail, kind=FailureKind.SERVER_ERROR, source=source)
    return ProviderError(detail, kind=FailureKind.SERVER_ERROR, source=source, retryable=False)


async def request_json(
    method: str,
    url: str,
    *,
    source: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
    retries: int = 2,
    user_agent: str = "Pathfinder/1.0",
) -> dict[str, Any]:
    last: ProviderError | None = None
    merged_headers = {"User-Agent": user_agent, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(
                    method, url, headers=merged_headers, params=params, json=payload
                )
            if response.status_code >= 400:
                raise classify_status(response.status_code, source, response.text[:300])
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError(
                    f"{source} returned non-JSON content",
                    kind=FailureKind.PARSE,
                    source=source,
                ) from exc
            if not isinstance(data, dict):
                return {"data": data}
            return data
        except ProviderNoData:
            raise
        except ProviderError as exc:
            last = exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last = ProviderError(
                f"{source} network failure: {exc.__class__.__name__}",
                kind=FailureKind.NETWORK,
                source=source,
            )
        except Exception as exc:  # defensive: never leak an SDK exception upward
            last = ProviderError(
                f"{source} failed: {exc.__class__.__name__}",
                kind=FailureKind.SERVER_ERROR,
                source=source,
                retryable=False,
            )
        if last is None or not last.retryable or attempt >= retries:
            break
        await asyncio.sleep((0.35 * (2**attempt)) + random.uniform(0.0, 0.25))
    assert last is not None
    raise last


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    radius = 6371.0
    d_lat = radians(b_lat - a_lat)
    d_lng = radians(b_lng - a_lng)
    h = sin(d_lat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(d_lng / 2) ** 2
    return 2 * radius * asin(sqrt(h))
