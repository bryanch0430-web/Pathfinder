/** Pure route geometry for the playback token.
 *
 * Leg paths are shared by hundreds of token frames, so cumulative distances are
 * computed once per leg (`buildGeometry`) and `pointAlong` searches that array
 * instead of re-summing `kmBetween` on every frame.
 */

export interface PathPoint {
  lat: number;
  lng: number;
}

export interface RouteGeometry {
  points: Array<PathPoint & { cumKm: number }>;
  totalKm: number;
}

function distanceKm(a: PathPoint, b: PathPoint): number {
  const dx = (a.lng - b.lng) * 111 * Math.cos((((a.lat + b.lat) / 2) * Math.PI) / 180);
  const dy = (a.lat - b.lat) * 111;
  return Math.hypot(dx, dy);
}

export function buildGeometry(path: PathPoint[]): RouteGeometry {
  const points: RouteGeometry["points"] = [];
  let total = 0;
  for (let index = 0; index < path.length; index += 1) {
    if (index > 0) total += distanceKm(path[index - 1], path[index]);
    points.push({ lat: path[index].lat, lng: path[index].lng, cumKm: total });
  }
  return { points, totalKm: total };
}

/** Shift a path sideways by `meters` (positive = right of travel).
 *
 * Two legs that use the same road (a hotel -> stop leg and its return, or a
 * duplicated segment) otherwise draw exactly on top of each other. Offsetting
 * one of them keeps both visible without changing the road it represents.
 */
export function offsetPath(path: PathPoint[], meters: number): PathPoint[] {
  if (path.length < 2 || meters === 0) return path.map((point) => ({ ...point }));
  return path.map((point, index) => {
    const previous = path[Math.max(0, index - 1)];
    const next = path[Math.min(path.length - 1, index + 1)];
    const dLat = next.lat - previous.lat;
    const dLng = next.lng - previous.lng;
    const length = Math.hypot(dLat, dLng) || 1;
    const normalLat = -dLng / length;
    const normalLng = dLat / length;
    const latShift = (meters / 111_320) * normalLat;
    const scale = Math.cos((point.lat * Math.PI) / 180);
    const lngShift = (meters / (111_320 * (Math.abs(scale) < 1e-6 ? 1 : scale))) * normalLng;
    return { lat: point.lat + latShift, lng: point.lng + lngShift };
  });
}

export function pointAlong(geometry: RouteGeometry, t: number): PathPoint {
  const points = geometry.points;
  if (points.length === 0) return { lat: 0, lng: 0 };
  if (points.length === 1) return { lat: points[0].lat, lng: points[0].lng };
  const clamped = Math.min(1, Math.max(0, t));
  if (geometry.totalKm === 0) return { lat: points[0].lat, lng: points[0].lng };
  const target = clamped * geometry.totalKm;
  // First vertex at or past the target; binary search over the cumulative array.
  let low = 1;
  let high = points.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (points[mid].cumKm < target) low = mid + 1;
    else high = mid;
  }
  const to = points[low];
  const from = points[low - 1];
  const segment = to.cumKm - from.cumKm;
  const local = segment === 0 ? 0 : (target - from.cumKm) / segment;
  return {
    lat: from.lat + (to.lat - from.lat) * local,
    lng: from.lng + (to.lng - from.lng) * local,
  };
}
