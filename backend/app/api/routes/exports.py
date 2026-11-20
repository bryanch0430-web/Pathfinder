from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.auth import RequestIdentity, get_request_identity
from app.services.enrichment import MAX_POINTS, locate_places

router = APIRouter(prefix="/api/exports", tags=["exports"])


class PointPlace(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    base: str = Field(min_length=1, max_length=160)


class PointsRequest(BaseModel):
    destination: str | None = Field(default=None, max_length=160)
    places: list[PointPlace] = Field(max_length=MAX_POINTS)


@router.post("/points")
async def points(request: PointsRequest, identity: RequestIdentity = Depends(get_request_identity)):
    """Coordinates for a trip's planned places, for the GPX and KML downloads."""
    if not identity.user_id and not identity.guest_id:  # not an open geocoding proxy
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in or continue as a guest.")
    found = await locate_places(request.destination, [(p.id, p.name, p.base) for p in request.places])
    return {"points": found, "source": {"name": "© OpenStreetMap contributors", "url": "https://www.openstreetmap.org/copyright"}}
