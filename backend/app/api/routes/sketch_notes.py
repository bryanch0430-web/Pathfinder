"""The traveler's drawings over sketchbook pages (Excalidraw scenes), per trip page. Owner only."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.trips import _owned_trip
from app.core.auth import RequestIdentity, get_request_identity
from app.core.database import get_db
from app.models.sketch_note import SketchNote

router = APIRouter(prefix="/api/trips", tags=["sketch-notes"])

PAGE_ID = r"^(overview|d[1-9][0-9]{0,2})$"
MAX_SCENE_BYTES = 2_000_000  # a page of pen strokes is a few kB; pasted photos are what this stops


class SceneBody(BaseModel):
    elements: list[dict] = Field(max_length=5000)
    files: dict[str, dict] = Field(default_factory=dict)


def _note(row: SketchNote) -> dict:
    return {"page_id": row.page_id, **row.scene, "updated_at": row.updated_at}


@router.get("/{trip_id}/sketch-notes")
async def list_sketch_notes(trip_id: uuid.UUID, identity: RequestIdentity = Depends(get_request_identity),
                            db: AsyncSession = Depends(get_db)):
    await _owned_trip(db, trip_id, identity)
    rows = (await db.execute(select(SketchNote).where(SketchNote.trip_id == trip_id))).scalars()
    return {"notes": {row.page_id: _note(row) for row in rows}}


@router.put("/{trip_id}/sketch-notes/{page_id}")
async def put_sketch_note(body: SceneBody, trip_id: uuid.UUID, page_id: str = Path(pattern=PAGE_ID),
                          identity: RequestIdentity = Depends(get_request_identity), db: AsyncSession = Depends(get_db)):
    """Replace a page's drawing. Deleted elements are dropped; an empty drawing removes the row."""
    await _owned_trip(db, trip_id, identity)
    elements = [element for element in body.elements if not element.get("isDeleted")]
    used = {element.get("fileId") for element in elements if element.get("fileId")}
    scene = {"elements": elements, "files": {key: value for key, value in body.files.items() if key in used}}
    if len(json.dumps(scene)) > MAX_SCENE_BYTES:
        raise HTTPException(413, "This drawing is too large to save (2 MB at most).")
    row = (await db.execute(select(SketchNote).where(SketchNote.trip_id == trip_id, SketchNote.page_id == page_id))).scalar_one_or_none()
    if not elements:
        if row:
            await db.delete(row)
        await db.commit()
        return {"page_id": page_id, "elements": [], "files": {}, "updated_at": None}
    if row:
        row.scene = scene
    else:
        row = SketchNote(trip_id=trip_id, page_id=page_id, scene=scene)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return _note(row)


@router.delete("/{trip_id}/sketch-notes/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sketch_note(trip_id: uuid.UUID, page_id: str = Path(pattern=PAGE_ID),
                             identity: RequestIdentity = Depends(get_request_identity), db: AsyncSession = Depends(get_db)):
    await _owned_trip(db, trip_id, identity)
    await db.execute(delete(SketchNote).where(SketchNote.trip_id == trip_id, SketchNote.page_id == page_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
