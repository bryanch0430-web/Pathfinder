import { useEffect, useState } from 'react';
import { apiFetch } from './apiClient';

export type AroundTab = 'see' | 'food' | 'stay' | 'landmarks';
export interface AroundItem {
  id: string; name: string; alt?: string | null;
  kind: 'see' | 'do' | 'buy' | 'eat' | 'drink' | 'sleep' | 'landmark' | 'transit'; tab: AroundTab;
  blurb?: string | null; price?: string | null; hours?: string | null; address?: string | null; website?: string | null;
  lat?: number | null; lon?: number | null; distance_km?: number | null;
  image?: string | null;
  image_credit?: { author?: string | null; license?: string | null; license_url?: string | null; file_page?: string | null } | null;
  source: 'Wikivoyage' | 'Wikipedia'; source_url?: string | null; wikipedia_url?: string | null;
}
export interface AroundHere { items: AroundItem[]; guides: { title: string; url: string }[]; available: boolean }

// Same ~1 km cell as the server cache, so neighbouring stops share one request per session.
const cache = new Map<string, Promise<AroundHere | null>>();
const cellOf = (lat: number, lon: number) => `${lat.toFixed(2)},${lon.toFixed(2)}`;

function load(lat: number, lon: number): Promise<AroundHere | null> {
  const key = cellOf(lat, lon);
  if (!cache.has(key)) {
    const request = apiFetch<AroundHere>(`/api/places/around?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`)
      .then((response) => response.ok && response.data?.available ? response.data : null).catch(() => null);
    // A failed lookup is forgotten so the next mount retries it.
    void request.then((data) => { if (!data) cache.delete(key); });
    cache.set(key, request);
  }
  return cache.get(key)!;
}

/** What's around a point: listings and landmarks, or null while loading / when unavailable. */
export function useAroundHere(lat?: number, lon?: number): { data: AroundHere | null; loading: boolean } {
  const key = lat !== undefined && lon !== undefined ? cellOf(lat, lon) : null;
  const [state, setState] = useState<{ key: string | null; data: AroundHere | null; done: boolean }>({ key: null, data: null, done: false });
  useEffect(() => {
    if (!key) return;
    let live = true;
    void load(lat!, lon!).then((data) => { if (live) setState({ key, data, done: true }); });
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps -- key captures lat/lon at cell precision
  const current = state.key === key;
  return { data: current ? state.data : null, loading: !!key && !(current && state.done) };
}
