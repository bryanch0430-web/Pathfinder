import { useEffect, useState } from 'react';
import { apiFetch } from './apiClient';

export interface SeasonNote { tone: 'good' | 'warn' | 'info'; text: string }
export interface StopSeason {
  id: string; month: number;
  /** "Typical for <month>" for climate averages; "Forecast for <day>" when kind is 'forecast'. */
  label: string;
  temp_c: number; rain_mm_day: number;
  badge: SeasonNote; notes: SeasonNote[];
  /** Set when the trip document's MET Norway forecast replaced the climate figure (inside the window). */
  kind?: 'forecast';
  source?: string;
}
export interface SeasonTarget { id: string; lat: number; lon: number; month: number; places: { name: string; category?: string }[] }

const cache = new Map<string, Record<string, StopSeason>>();
const EMPTY: Record<string, StopSeason> = {};

/** Season tips per stop id. Keeps the last answer while a streamed graph changes, so badges don't flicker. */
export function useSeason(targets: SeasonTarget[]): Record<string, StopSeason> {
  const signature = JSON.stringify(targets);
  const [last, setLast] = useState(EMPTY);
  useEffect(() => {
    if (!targets.length) return;
    const hit = cache.get(signature);
    if (hit) { setLast(hit); return; }
    let live = true;
    void apiFetch<{ stops: StopSeason[] }>('/api/places/season', { method: 'POST', body: JSON.stringify({ stops: targets.slice(0, 24) }) })
      .then((response) => {
        if (!response.ok || !response.data) return; // not remembered: retried when the stops next change
        const data = Object.fromEntries(response.data.stops.map((stop) => [stop.id, stop]));
        cache.set(signature, data);
        if (live) setLast(data);
      }).catch(() => undefined);
    return () => { live = false; };
  }, [signature]); // eslint-disable-line react-hooks/exhaustive-deps -- signature captures targets
  return targets.length ? cache.get(signature) || last : EMPTY;
}
