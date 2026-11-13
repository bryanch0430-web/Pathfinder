/**
 * What the trip map shows for a day of a trip of one or several cities: the centre of the city, the stay of that night,
 * the stops near enough to belong to it, the line a travel day draws from the city before, and the cities of the whole
 * trip. Pure functions on the plan, so they are tested without a map.
 */
import type { GeoPoint, HotelStay, TripPlan, TripSegment } from "../design-system/types";
import { chosenFor, legInto, segmentOfDay, segmentsOf, stayOn } from "./segments";

/** A stop farther than this from the centre of its city is not drawn with it. */
export const DAY_RADIUS_KM = 150;

/** Straight-line kilometres between two points. */
export function distanceKm(a: GeoPoint, b: GeoPoint): number {
  const rad = (degrees: number) => (degrees * Math.PI) / 180;
  const h =
    Math.sin(rad(b.lat - a.lat) / 2) ** 2 +
    Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(rad(b.lng - a.lng) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** True when `geo` is within `km` of `centre`; with no centre or no point there is nothing to measure against. */
export function withinKm(centre: GeoPoint | null | undefined, geo: GeoPoint | null | undefined, km = DAY_RADIUS_KM): boolean {
  return !centre || !geo || distanceKm(centre, geo) <= km;
}

export interface DayScope {
  segment: TripSegment;
  /** The centre of the city; null when the source gave the city no coordinates. */
  centre: GeoPoint | null;
  /** The stay of that night, when it is near its city. */
  stay: HotelStay | null;
}

export function dayScope(plan: TripPlan, date: string): DayScope {
  const segment = segmentOfDay(plan, date);
  const centre = segment.destination.geo;
  const stay = stayOn(plan, date);
  return { segment, centre, stay: stay && withinKm(centre, stay.geo) ? stay : null };
}

export interface TravelLine {
  from: GeoPoint;
  to: GeoPoint;
  /** The mode of the option chosen for the leg ("train", "flight"); "ground" when the leg has none. */
  mode: string;
  label: string;
}

/** The line a travel day draws, from the city before to this one; null on any other day. */
export function travelLine(plan: TripPlan, date: string): TravelLine | null {
  const segments = segmentsOf(plan);
  const segment = segmentOfDay(plan, date);
  const index = segments.findIndex((entry) => entry.id === segment.id);
  if (index <= 0 || date !== segment.dates.start) return null;
  const before = segments[index - 1].destination;
  const here = segment.destination;
  if (!before.geo || !here.geo) return null;
  const leg = legInto(plan, segment.id);
  return {
    from: before.geo,
    to: here.geo,
    mode: (leg && chosenFor(plan, leg.id)?.mode) || "ground",
    label: `${before.name} → ${here.name}`,
  };
}

export interface OverviewStop {
  /** 1-based, in travel order (a city visited twice has two). */
  number: number;
  name: string;
  geo: GeoPoint;
  segmentId: string;
}

/** Every city of the trip with coordinates, in order: the numbered markers of the whole-trip view. */
export function overviewStops(plan: TripPlan): OverviewStop[] {
  const stops: OverviewStop[] = [];
  segmentsOf(plan).forEach((segment, index) => {
    if (segment.destination.geo) {
      stops.push({ number: index + 1, name: segment.destination.name, geo: segment.destination.geo, segmentId: segment.id });
    }
  });
  return stops;
}
