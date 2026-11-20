"""The traveler's drawings over sketch pages (/api/trips/{id}/sketch-notes)."""

import uuid

from httpx import AsyncClient

from app.api.routes.sketch_notes import MAX_SCENE_BYTES

STROKE = {"id": "a1", "type": "freedraw", "x": 10, "y": 20, "points": [[0, 0], [5, 5]], "isDeleted": False}


async def _trip(client: AsyncClient, headers: dict) -> str:
    return (await client.post("/api/trips", headers=headers)).json()["trip_id"]


async def test_save_list_replace_and_clear_a_page(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip = await _trip(client, headers)
    assert (await client.get(f"/api/trips/{trip}/sketch-notes", headers=headers)).json() == {"notes": {}}

    gone = {**STROKE, "id": "a2", "isDeleted": True}
    image = {"id": "a3", "type": "image", "fileId": "f1", "x": 0, "y": 0}
    body = {"elements": [STROKE, gone, image], "files": {"f1": {"id": "f1", "dataURL": "data:x"}, "unused": {"id": "unused"}}}
    saved = (await client.put(f"/api/trips/{trip}/sketch-notes/d2", json=body, headers=headers)).json()
    assert [e["id"] for e in saved["elements"]] == ["a1", "a3"]  # deleted elements dropped
    assert list(saved["files"]) == ["f1"]  # files no element uses are dropped

    await client.put(f"/api/trips/{trip}/sketch-notes/d2", json={"elements": [STROKE]}, headers=headers)
    notes = (await client.get(f"/api/trips/{trip}/sketch-notes", headers=headers)).json()["notes"]
    assert list(notes) == ["d2"] and [e["id"] for e in notes["d2"]["elements"]] == ["a1"]  # replaced, not appended

    cleared = await client.put(f"/api/trips/{trip}/sketch-notes/d2", json={"elements": [gone]}, headers=headers)
    assert cleared.json()["elements"] == []
    assert (await client.get(f"/api/trips/{trip}/sketch-notes", headers=headers)).json() == {"notes": {}}


async def test_only_the_owner_and_only_real_page_ids(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip = await _trip(client, headers)
    body = {"elements": [STROKE]}
    stranger = {"X-Guest-ID": str(uuid.uuid4())}
    assert (await client.put(f"/api/trips/{trip}/sketch-notes/overview", json=body, headers=stranger)).status_code == 403
    assert (await client.get(f"/api/trips/{trip}/sketch-notes", headers=stranger)).status_code == 403
    for bad in ("d0", "day-1", "overview2", "d1000"):
        assert (await client.put(f"/api/trips/{trip}/sketch-notes/{bad}", json=body, headers=headers)).status_code == 422
    assert (await client.put(f"/api/trips/{trip}/sketch-notes/overview", json=body, headers=headers)).status_code == 200
    assert (await client.delete(f"/api/trips/{trip}/sketch-notes/overview", headers=headers)).status_code == 204
    assert (await client.get(f"/api/trips/{trip}/sketch-notes", headers=headers)).json() == {"notes": {}}


async def test_oversized_drawings_are_refused(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip = await _trip(client, headers)
    image = {"id": "a3", "type": "image", "fileId": "f1", "x": 0, "y": 0}
    huge = {"elements": [image], "files": {"f1": {"id": "f1", "dataURL": "x" * (MAX_SCENE_BYTES + 1)}}}
    response = await client.put(f"/api/trips/{trip}/sketch-notes/d1", json=huge, headers=headers)
    assert response.status_code == 413 and "too large" in response.json()["detail"]
