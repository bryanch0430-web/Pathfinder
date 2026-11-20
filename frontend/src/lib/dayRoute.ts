/**
 * A day as a timed route: the original Pathfinder map plays the day's stops
 * in order, leaves the stay, and comes back. Clock times in the plan win.
 * A day that only names a part of the day gets planning slots so the map
 * still has something to walk.
 */

export interface TimedItem {
  name: string;
  category: string;
  time_of_day?: 'morning' | 'afternoon' | 'evening' | null;
  start?: string | null;
  end?: string | null;
  duration_hours?: number | null;
  option?: boolean;
}

export interface ScheduledStop {
  key: string;
  name: string;
  category: string;
  time_of_day: 'morning' | 'afternoon' | 'evening' | null;
  startMin: number;
  endMin: number;
}

export interface LatLng {
  lat: number;
  lng: number;
}

export interface RoutePoint {
  id: string;
  name: string;
  lat: number;
  lon: number;
  category: string;
  start: number;
  end: number;
}

export interface RouteLeg {
  fromId: string;
  toId: string;
  path: LatLng[];
}

export interface DayRouteView {
  points: RoutePoint[];
  legs: RouteLeg[];
}

export interface PlaybackFrame {
  clock: number;
  playing: boolean;
  follow: boolean;
}

const SLOT_START = { morning: 9 * 60, afternoon: 13 * 60, evening: 18 * 60 } as const;
const SLOT_END = { morning: 12 * 60, afternoon: 17 * 60, evening: 21 * 60 } as const;

export function clockOf(value: string | null | undefined): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec((value ?? '').trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return hour * 60 + minute;
}

export function formatClock(minutes: number): string {
  const wrapped = ((Math.round(minutes) % (24 * 60)) + 24 * 60) % (24 * 60);
  return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`;
}

export function placeKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'place';
}

export function scheduleDay(items: TimedItem[]): ScheduledStop[] {
  const planned = items.filter((item) => !item.option);
  const stated = planned.some((item) => clockOf(item.start) != null);
  const rows: ScheduledStop[] = [];
  let cursor = 9 * 60;
  planned.forEach((item, index) => {
    const start = clockOf(item.start);
    const end = clockOf(item.end);
    const duration = Math.max(30, Math.round((item.duration_hours || 1.5) * 60));
    const slot = item.time_of_day && item.time_of_day in SLOT_START ? item.time_of_day : null;
    let from: number;
    let to: number;
    if (start != null) {
      from = start;
      to = end != null && end > start ? end : start + duration;
    } else if (slot && !stated) {
      const used = rows.filter((row) => row.time_of_day === slot).reduce((sum, row) => sum + (row.endMin - row.startMin), 0);
      const open = SLOT_START[slot];
      const close = SLOT_END[slot];
      from = Math.min(open + used, close - 30);
      to = Math.max(from + 30, Math.min(from + duration, close));
    } else {
      from = Math.max(cursor, slot ? SLOT_START[slot] : cursor);
      to = from + duration;
    }
    cursor = Math.max(cursor, to);
    rows.push({
      key: `${placeKey(item.name)}-${index}`,
      name: item.name,
      category: item.category || 'other',
      time_of_day: slot,
      startMin: from,
      endMin: to,
    });
  });
  return rows;
}

/** Leave the stay, visit the day in order, and return. A day with no stay is just its stops. */
export function routeChain(rows: ScheduledStop[]): ScheduledStop[] {
  const stay = rows.find((row) => row.category === 'stay');
  const visits = rows.filter((row) => row.category !== 'stay');
  if (!stay || !visits.length) return rows;
  const first = visits[0].startMin;
  const last = visits[visits.length - 1].endMin;
  const leave = Math.max(0, first - 20);
  const departEnd = Math.min(first, leave + 10);
  return [
    { ...stay, key: `${stay.key}-out`, startMin: leave, endMin: Math.max(leave + 1, departEnd) },
    ...visits,
    { ...stay, key: `${stay.key}-back`, startMin: last, endMin: last + 20 },
  ];
}

export function haversineKm(a: LatLng, b: LatLng): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function pointAlong(path: LatLng[], t: number): LatLng {
  if (!path.length) return { lat: 0, lng: 0 };
  if (path.length === 1 || t <= 0) return path[0];
  if (t >= 1) return path[path.length - 1];
  const lengths = path.slice(1).map((point, index) => haversineKm(path[index], point));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  if (!total) return path[0];
  let remain = t * total;
  for (let index = 0; index < lengths.length; index += 1) {
    const span = lengths[index];
    if (remain <= span || index === lengths.length - 1) {
      const share = span ? Math.min(1, remain / span) : 0;
      const from = path[index];
      const to = path[index + 1];
      return { lat: from.lat + (to.lat - from.lat) * share, lng: from.lng + (to.lng - from.lng) * share };
    }
    remain -= span;
  }
  return path[path.length - 1];
}

export function splitPath(path: LatLng[], t: number): { done: LatLng[]; ahead: LatLng[] } {
  if (path.length < 2 || t <= 0) return { done: [], ahead: path };
  if (t >= 1) return { done: path, ahead: [] };
  const cut = pointAlong(path, t);
  const lengths = path.slice(1).map((point, index) => haversineKm(path[index], point));
  const total = lengths.reduce((sum, length) => sum + length, 0) || 1;
  let remain = t * total;
  const done: LatLng[] = [path[0]];
  for (let index = 0; index < lengths.length; index += 1) {
    if (remain <= lengths[index]) {
      done.push(cut);
      return { done, ahead: [cut, ...path.slice(index + 1)] };
    }
    done.push(path[index + 1]);
    remain -= lengths[index];
  }
  return { done: path, ahead: [] };
}

/** Where the playback token is. `leg` is set only while it is between two stops. */
export function placeOnRoute(
  points: { start: number; end: number }[],
  clock: number,
): { at: number; leg: number | null; t: number } {
  if (!points.length) return { at: 0, leg: null, t: 0 };
  if (clock <= points[0].start) return { at: 0, leg: null, t: 0 };
  for (let index = 0; index < points.length; index += 1) {
    const here = points[index];
    if (clock <= here.end) return { at: index, leg: null, t: 0 };
    const next = points[index + 1];
    if (!next) return { at: index, leg: null, t: 1 };
    if (clock < next.start) {
      const span = Math.max(1, next.start - here.end);
      return { at: index, leg: index, t: Math.min(1, (clock - here.end) / span) };
    }
  }
  return { at: points.length - 1, leg: null, t: 1 };
}
