from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import RequestIdentity, get_request_identity
from app.schemas.places import PlaceMediaRequest, SeasonRequest
from app.services.around_here import get_around_here
from app.services.place_search import google_nearby, merge_places
from app.services.place_media import get_place_media
from app.services.seasonality import get_season

router = APIRouter(prefix="/api/places", tags=["places"])


@router.post("/media")
async def place_media(
    request: PlaceMediaRequest,
    identity: RequestIdentity = Depends(get_request_identity),
):
    """Credited, free-licensed photos for itinerary places, for signed-in users and guests alike."""
    _require_identity(identity)
    return await get_place_media(request)


@router.get("/around")
async def around_here(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    identity: RequestIdentity = Depends(get_request_identity),
):
    """What to see, eat and where to stay near a stop. Google Places first, then the guides."""
    _require_identity(identity)
    base = await get_around_here(lat, lon)
    extra = await google_nearby(lat, lon)
    items = merge_places(extra, base.get("items") or [])
    return {**base, "items": items, "available": bool(items) or bool(base.get("available"))}


@router.post("/season")
async def season(
    request: SeasonRequest,
    identity: RequestIdentity = Depends(get_request_identity),
):
    """Typical weather per stop for its month, with deterministic packing and place tips."""
    _require_identity(identity)
    return await get_season(request)


def _require_identity(identity: RequestIdentity) -> None:
    # get_request_identity accepts a request with neither identity; refuse it so we aren't an open proxy.
    if not identity.user_id and not identity.guest_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in or continue as a guest.")
