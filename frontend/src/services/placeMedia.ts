import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from './apiClient';

export interface PlaceMedia {
  title: string;
  description?: string | null;
  extract?: string | null;
  article_url?: string | null;
  image: string;
  width?: number;
  height?: number;
  credit: { author?: string | null; license?: string | null; license_url?: string | null; file_page?: string | null };
}
export interface MediaTarget { id: string; name: string; lat?: number; lon?: number }

// Session cache keyed like the server's (name + 2-decimal point), so the same place shared by
// several views is fetched once. null = looked up, nothing usable.
const cache = new Map<string, PlaceMedia | null>();
const pending = new Set<string>();
const listeners = new Set<() => void>();
const keyOf = (t: MediaTarget) => `${t.name.trim().toLowerCase()}|${t.lat !== undefined && t.lon !== undefined ? `${t.lat.toFixed(2)},${t.lon.toFixed(2)}` : '-'}`;

async function load(targets: MediaTarget[]) {
  for (let i = 0; i < targets.length; i += 24) {
    const batch = targets.slice(i, i + 24);
    const response = await apiFetch<{ places: (PlaceMedia & { id: string })[] }>('/api/places/media', {
      method: 'POST',
      body: JSON.stringify({ places: batch.map((t, index) => ({ id: String(index), name: t.name, lat: t.lat, lon: t.lon })) }),
    }).catch(() => null);
    const found = new Map((response?.ok ? response.data?.places || [] : []).map((item) => [Number(item.id), item]));
    batch.forEach((t, index) => {
      pending.delete(keyOf(t));
      // A failed request is not remembered: it retries when the place list next changes or the view remounts.
      if (response?.ok) cache.set(keyOf(t), found.get(index) || null);
    });
  }
  listeners.forEach((notify) => notify());
}

/** Photos for the given places, filled in as they arrive. Keyed by each target's id. */
export function usePlaceMedia(targets: MediaTarget[]): Record<string, PlaceMedia> {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const notify = () => setTick((tick) => tick + 1);
    listeners.add(notify);
    return () => { listeners.delete(notify); };
  }, []);
  const signature = targets.map(keyOf).join(';');
  useEffect(() => {
    const unique = new Map(targets.filter((t) => t.name.trim()).map((t) => [keyOf(t), t]));
    const todo = [...unique.entries()].filter(([key]) => !cache.has(key) && !pending.has(key));
    if (!todo.length) return;
    todo.forEach(([key]) => pending.add(key));
    void load(todo.map(([, t]) => t));
  }, [signature]); // eslint-disable-line react-hooks/exhaustive-deps -- signature captures targets
  // Stable identity until a photo arrives: views rebuild map overlays when this object changes.
  return useMemo(() => Object.fromEntries(targets.flatMap((t) => {
    const media = cache.get(keyOf(t));
    return media ? [[t.id, media]] : [];
  })), [targets.map((t) => t.id).join(';'), signature, tick]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** The same Commons thumbnail at another width (thumb URLs end in /<width>px-<file>). Use a standard
 *  Wikimedia step (120, 250, 330, 500, 960) for small views so they don't download the 960 px image. */
export const sizedImage = (url: string, width: number) => url.replace(/\/\d+px-([^/]+)$/, `/${width}px-$1`);

export const creditLine = (media: PlaceMedia) =>
  ['Photo: Wikimedia Commons', media.credit.author, media.credit.license].filter(Boolean).join(' · ');
