/* Google Maps directions links, one per day, from place names (no API key, no coordinates needed). */
import type { TripDocument } from '../../services/tripService';

type Day = TripDocument['days'][number];
export const MAX_STOPS = 9; // origin + 7 waypoints + destination: what the mobile apps reliably accept
const MODE: Record<string, string> = { walk: 'walking', transit: 'transit', taxi: 'driving', drive: 'driving', car: 'driving' };

/** Directions through a day's planned places (options left out), or null when the day has none. */
export function dayDirectionsUrl(day: Day, travelMode = 'transit'): string | null {
  const stops = day.items.filter((item) => !item.option).map((item) => `${item.name}, ${day.base}`).slice(0, MAX_STOPS);
  if (!stops.length) return null;
  if (stops.length === 1) {
    return `https://www.google.com/maps/search/?${new URLSearchParams({ api: '1', query: stops[0] })}`;
  }
  const params = new URLSearchParams({ api: '1', origin: stops[0], destination: stops.at(-1)! });
  if (stops.length > 2) params.set('waypoints', stops.slice(1, -1).join('|'));
  const mode = MODE[travelMode] || 'transit';
  // Google Maps ignores waypoints in transit mode, so let the app choose the mode when there are any.
  if (mode !== 'transit' || stops.length === 2) params.set('travelmode', mode);
  return `https://www.google.com/maps/dir/?${params}`;
}
