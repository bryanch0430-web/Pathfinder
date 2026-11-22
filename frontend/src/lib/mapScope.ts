/**
 * What the trip map shows for a day of a trip of one or several cities: the centre of the city, the stay of that night,
 * the stops near enough to belong to it, the line a travel day draws from the city before, and the cities of the whole
 * trip. Pure functions on the plan, so they are tested without a map.
 */
import type { GeoPoint, HotelStay, PlanItem, TripPlan, TripSegment } from "../design-system/types";
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

function finitePoint(point: GeoPoint | null | undefined): point is GeoPoint {
  if (!point) return false;
  return Number.isFinite(point.lat) && Number.isFinite(point.lng) && Math.abs(point.lat) <= 90 && Math.abs(point.lng) <= 180;
}

/**
 * The points a day's camera may frame. A pin farther than the day radius from the city, or a coordinate that is not a
 * place, is left out. One of those used to fit the whole world, and the fit ran again whenever the day was redrawn.
 */
export function cameraPoints(centre: GeoPoint | null | undefined, points: Array<GeoPoint | null | undefined>): GeoPoint[] {
  const finite = points.filter(finitePoint);
  if (!centre || !finitePoint(centre)) return finite;
  const near = finite.filter((point) => distanceKm(centre, point) <= DAY_RADIUS_KM);
  return near.length ? near : [centre];
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

/** The day's route: the stay, then every stop that has a location, then back to the stay.

An airport taxi with coordinates is the first point on an arrival day and the last on a departure day.
A day that leaves for the airport does not also draw a return to the hotel after that taxi.
*/
export function dayRoute(items: PlanItem[], hotel: PlanItem | null): PlanItem[] {
  const minutes = (value: string | null | undefined) => {
    if (!value || !value.includes(":")) return null;
    const [hour, minute] = value.split(":").map(Number);
    if (!Number.isFinite(hour) || !Number.isFinite(minute) || hour > 23) return null;
    return hour * 60 + minute;
  };
  const clock = (value: number) => {
    const whole = Math.max(0, Math.min(23 * 60 + 59, Math.floor(value)));
    return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
  };
  const ordered = [...items].sort((a, b) => (minutes(a.start) ?? 0) - (minutes(b.start) ?? 0));
  const arrivals = ordered.filter((item) => item.id.startsWith("transit_to_stay_"));
  const departures = ordered.filter((item) => item.id.startsWith("transit_from_stay_"));
  const stops = ordered.filter((item) => item.geo && !item.id.startsWith("transit_to_stay_") && !item.id.startsWith("transit_from_stay_"));
  const first = minutes(stops[0]?.start) ?? 9 * 60;
  const last = stops.reduce((latest, item) => Math.max(latest, minutes(item.end) ?? minutes(item.start) ?? latest), first);
  const chain: PlanItem[] = [];
  for (const item of arrivals) if (item.geo) chain.push(item);
  if (hotel?.geo) {
    const depart = Math.max(0, first - 20);
    chain.push({ ...hotel, start: clock(depart), end: clock(depart + 10) });
  }
  chain.push(...stops);
  const leaving = departures.filter((item) => item.geo);
  if (leaving.length) chain.push(...leaving);
  else if (hotel?.geo && stops.length) {
    const back = Math.min(23 * 60 + 40, last + 15);
    chain.push({ ...hotel, start: clock(back), end: clock(Math.min(23 * 60 + 59, back + 15)) });
  }
  return chain.filter((item, index) => index === 0 || item.id !== chain[index - 1].id || item.start !== chain[index - 1].start);
}
export function overviewStops(plan: TripPlan): OverviewStop[] {
  const stops: OverviewStop[] = [];
  segmentsOf(plan).forEach((segment, index) => {
    if (segment.destination.geo) {
      stops.push({ number: index + 1, name: segment.destination.name, geo: segment.destination.geo, segmentId: segment.id });
    }
  });
  return stops;
}
