/*
 * Day covers for the studio's day rail: a credited Wikimedia photo per day, from the day's first
 * planned place, falling back to its base city. Lookups are anchored at the base's position from
 * the trip document (enrichment.places), so the server's 20 km check rejects namesakes elsewhere.
 */
import { useMemo } from 'react';
import { type MediaTarget, type PlaceMedia, usePlaceMedia } from '../../services/placeMedia';
import type { TripDocument } from '../../services/tripService';

export function useDayCovers(doc: TripDocument | null): Record<number, PlaceMedia> {
  const days = doc?.days;
  const places = doc?.enrichment?.places;
  const targets = useMemo<MediaTarget[]>(() => (days ?? []).flatMap((day) => {
    const base = places?.find((place) => place.name === day.base);
    if (!base) return [];
    const first = day.items.find((item) => !item.option);
    return [
      ...(first ? [{ id: `item-${day.day}`, name: first.name, lat: base.lat, lon: base.lon }] : []),
      { id: `base-${day.day}`, name: day.base, lat: base.lat, lon: base.lon },
    ];
  }), [days, places]);
  const media = usePlaceMedia(targets);
  return useMemo(() => Object.fromEntries((days ?? []).flatMap((day) => {
    const photo = media[`item-${day.day}`] ?? media[`base-${day.day}`];
    return photo ? [[day.day, photo]] : [];
  })), [days, media]);
}
