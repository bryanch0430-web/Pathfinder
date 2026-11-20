// Coordinates for itinerary stops, shared by the route & map panel and the chat's photo strip.
// Open-Meteo's geocoder first (free, browser-side), then the trip's server geocoder for misses.
import { useEffect, useState } from 'react';
import type { Coordinates, ItineraryGraph } from './itineraryGraph';
import { getTripGeocodes } from '../../services/tripService';

const geocodeCache = new Map<string, Coordinates | null>();

async function geocode(name: string, destination: string, isOrigin: boolean, signal: AbortSignal): Promise<Coordinates | null> {
  const key = `${name}|${isOrigin ? '' : destination}`.toLowerCase();
  if (geocodeCache.has(key)) return geocodeCache.get(key) || null;
  const placeName = name.split(',')[0].replace(/\s+(Airport|Station)$/i, '').trim();
  const countryHint = (isOrigin ? name : destination).split(',').at(-1)?.trim().toLowerCase();
  const queries = isOrigin || name.toLowerCase() === destination.toLowerCase()
    ? [name] : [...new Set([`${name}, ${destination}`, name, `${placeName}, ${destination}`, placeName])];
  for (const query of queries) {
    try {
      const url = new URL('https://geocoding-api.open-meteo.com/v1/search');
      url.searchParams.set('name', query);
      url.searchParams.set('count', '5');
      url.searchParams.set('language', 'en');
      const response = await fetch(url, { signal });
      if (!response.ok) continue;
      const results: { name: string; country?: string; latitude: number; longitude: number }[] = (await response.json()).results || [];
      const inCountry = countryHint && (isOrigin ? name.includes(',') : destination.includes(','))
        ? results.filter((result) => result.country?.toLowerCase() === countryHint) : results;
      const normalize = (value: string) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();
      const found = inCountry.find((result) => normalize(result.name) === normalize(placeName));
      if (found && Number.isFinite(found.latitude) && Number.isFinite(found.longitude)) {
        const point = { lat: found.latitude, lon: found.longitude };
        geocodeCache.set(key, point);
        return point;
      }
    } catch {
      if (signal.aborted) return null;
    }
  }
  geocodeCache.set(key, null);
  return null;
}

/** Locate every stop in the graph. Pass `graph = null` to pause (e.g. while a panel is closed). */
export function useStopCoordinates(graph: ItineraryGraph | null | undefined, destination?: string, tripId?: string) {
  const [coordinates, setCoordinates] = useState<Record<string, Coordinates>>({});
  const [locating, setLocating] = useState(false);
  // A different trip must not show the previous trip's points; streamed graphs of the same trip keep them.
  useEffect(() => { setCoordinates({}); }, [tripId]);
  useEffect(() => {
    if (!graph?.nodes.length) return;
    const controller = new AbortController();
    setLocating(true);
    void Promise.all(graph.nodes.map(async (node) => [node.id,
      await geocode(node.name, destination || '', node.kind === 'origin', controller.signal),
    ] as const)).then(async (rows) => {
      if (controller.signal.aborted) return;
      const located = Object.fromEntries(rows.filter((row): row is readonly [string, Coordinates] => row[1] !== null));
      setCoordinates(located);
      const missing = graph.nodes.filter((node) => !located[node.id]);
      const fallback = tripId && missing.length
        ? await getTripGeocodes(tripId, missing.map((node) => ({ id: node.id, name: node.name, is_origin: node.kind === 'origin' }))).catch(() => ({})) : {};
      if (!controller.signal.aborted) { setCoordinates({ ...located, ...fallback }); setLocating(false); }
    });
    return () => controller.abort();
  }, [graph, destination, tripId]);
  return { coordinates, locating };
}
