/*
 * The traveler's drawings over sketch pages (backend /api/trips/{id}/sketch-notes): one Excalidraw
 * scene per page id, in page coordinates, so a drawing sits exactly where it was drawn.
 * Loaded once per trip and shared by the overlay and the editor.
 */
import { useEffect, useState } from 'react';
import { apiFetch } from '../../../services/apiClient';

/** Excalidraw's own element and file records, stored as the editor produced them. */
export interface SketchScene { elements: Record<string, unknown>[]; files: Record<string, Record<string, unknown>> }
type Notes = Record<string, SketchScene>;

const cache = new Map<string, Notes>();
const loading = new Set<string>();
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

async function load(tripId: string) {
  if (cache.has(tripId) || loading.has(tripId)) return;
  loading.add(tripId);
  const response = await apiFetch<{ notes: Notes }>(`/api/trips/${tripId}/sketch-notes`, { method: 'GET' }).catch(() => null);
  loading.delete(tripId);
  if (response?.ok && response.data) {
    cache.set(tripId, response.data.notes);
    notify();
  }
}

/** The trip's drawings by page id; empty until loaded, and when there are none. */
export function useSketchNotes(tripId: string): Notes {
  const [, setTick] = useState(0);
  useEffect(() => {
    const listener = () => setTick((tick) => tick + 1);
    listeners.add(listener);
    void load(tripId);
    return () => { listeners.delete(listener); };
  }, [tripId]);
  return cache.get(tripId) ?? {};
}

/** Save (or, with no elements, clear) one page's drawing. Throws a readable message on failure. */
export async function saveSketchNote(tripId: string, pageId: string, scene: SketchScene) {
  const response = await apiFetch<SketchScene>(`/api/trips/${tripId}/sketch-notes/${pageId}`, {
    method: 'PUT', body: JSON.stringify(scene),
  });
  if (!response.ok || !response.data) throw new Error(response.error || 'Your drawing could not be saved.');
  const notes = { ...(cache.get(tripId) ?? {}) };
  if (response.data.elements.length) notes[pageId] = { elements: response.data.elements, files: response.data.files };
  else delete notes[pageId];
  cache.set(tripId, notes);
  notify();
}
