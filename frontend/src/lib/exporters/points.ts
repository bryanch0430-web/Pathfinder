/* Coordinates for the trip's places (from backend POST /api/exports/points), shared by GPX and KML. */
import type { TripDocument } from '../../services/tripService';

export interface LatLon { lat: number; lon: number }
/** Keyed by pointKey(): "d2-0" for day 2's first item. Unlocated places are simply absent. */
export type PlacePoints = Record<string, LatLon>;

export const pointKey = (day: number, index: number) => `d${day}-${index}`;

/** The request body for /api/exports/points. */
export const pointsRequest = (doc: TripDocument) => ({
  destination: doc.destination,
  places: doc.days.flatMap((day) => day.items.map((item, index) => ({ id: pointKey(day.day, index), name: item.name, base: day.base }))),
});

/** The day's located places, in plan order. */
export const locatedStops = (day: TripDocument['days'][number], points: PlacePoints) =>
  day.items.flatMap((item, index) => {
    const point = points[pointKey(day.day, index)];
    return point ? [{ item, point }] : [];
  });

export const xml = (value: string) =>
  // eslint-disable-next-line no-control-regex -- strip characters XML 1.0 forbids
  value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
