import assert from "node:assert/strict";
import test from "node:test";

import { buildGeometry, offsetPath, pointAlong } from "./mapGeometry.ts";

test("cumulative distance is built once and monotonic", () => {
  const geometry = buildGeometry([
    { lat: 0, lng: 0 },
    { lat: 0, lng: 1 },
    { lat: 0, lng: 2 },
  ]);
  assert.equal(geometry.points.length, 3);
  assert.equal(geometry.points[0].cumKm, 0);
  assert.ok(geometry.points[1].cumKm > 100);
  assert.ok(geometry.points[2].cumKm > geometry.points[1].cumKm);
  assert.ok(Math.abs(geometry.totalKm - 222.4) < 0.5);
});

test("pointAlong interpolates by distance and clamps the ends", () => {
  const geometry = buildGeometry([
    { lat: 0, lng: 0 },
    { lat: 0, lng: 1 },
    { lat: 0, lng: 2 },
  ]);
  const middle = pointAlong(geometry, 0.5);
  assert.ok(Math.abs(middle.lat) < 1e-6);
  assert.ok(Math.abs(middle.lng - 1) < 1e-6);
  assert.deepEqual(pointAlong(geometry, -1), { lat: 0, lng: 0 });
  assert.deepEqual(pointAlong(geometry, 2), { lat: 0, lng: 2 });
});

test("degenerate paths do not throw", () => {
  const empty = buildGeometry([]);
  assert.deepEqual(pointAlong(empty, 0.5), { lat: 0, lng: 0 });
  const single = buildGeometry([{ lat: 1, lng: 2 }]);
  assert.deepEqual(pointAlong(single, 0.7), { lat: 1, lng: 2 });
  const repeated = buildGeometry([
    { lat: 1, lng: 2 },
    { lat: 1, lng: 2 },
  ]);
  assert.equal(repeated.totalKm, 0);
  assert.deepEqual(pointAlong(repeated, 0.4), { lat: 1, lng: 2 });
});

test("offsetPath separates two legs on the same road", () => {
  const path = [
    { lat: 0, lng: 0 },
    { lat: 0, lng: 0.01 },
  ];
  const shifted = offsetPath(path, 10);
  // Travelling east, "right" is south: latitude drops by ~10 m.
  assert.ok(shifted[0].lat < 0);
  assert.ok(Math.abs(shifted[0].lat + 10 / 111320) < 1e-9);
  assert.ok(Math.abs(shifted[0].lng) < 1e-12);
  const reverse = offsetPath([...path].reverse(), 10);
  assert.ok(reverse[0].lat > 0);
  assert.equal(offsetPath(path, 0)[0].lat, 0);
});
