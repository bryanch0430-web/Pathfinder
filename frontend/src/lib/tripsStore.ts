/**
 * The open trip tabs, shared by every window of the browser.
 *
 * They live in `pathfinder.trips` as `{ v: 1, trips: [{ id, label }] }`. Things that used to go wrong
 * with a bare array read and written from component state:
 *
 * - A damaged value (not JSON, or JSON of the wrong shape) broke the tab bar, or came back
 *   on every load. Here an unreadable value reads as no tabs and is written back clean, so
 *   the next read is fine. A bare array from an earlier build is carried over once.
 * - A second window kept its own copy and wrote it over the first one's tabs. Here every
 *   write starts from what is stored right now, and the `storage` event tells the other
 *   windows to read it again. There is no lock: two windows that change the tabs at the very
 *   same moment can still lose one of the two changes.
 * - A browser that refuses the write (quota, a blocked store) lost every tab but the newest, because
 *   each write started again from the old stored list. Here the tabs the browser would not keep are held
 *   in this window and stand in for the stored list until a write goes through, so they still work,
 *   they are just not kept.
 * - A store written by a newer build (`v` above ours) was overwritten with an empty list. Here it is
 *   left as it is: its tabs are shown, and this window's changes are held in the window.
 */
import { storageKey } from "./storage";

export interface Trip {
  id: string;
  label: string;
}

const KEY = storageKey("trips");
const VERSION = 1;

/** Listeners in this window. The browser only fires `storage` in the other windows. */
const listeners = new Set<(trips: Trip[]) => void>();

/** The tabs of this window that the browser would not store; they stand in for the stored list until a write goes through. */
let unsaved: Trip[] | null = null;
/** The store cannot be read, or a newer build wrote it: the store is left alone and this window's changes are held in the window. */
let heldBack = false;

function safeStorage(): Storage | null {
  try {
    // Accessing localStorage throws in some privacy modes and sandboxed frames.
    return window.localStorage;
  } catch {
    return null;
  }
}

function isTrip(value: unknown): value is Trip {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Record<string, unknown>;
  return typeof row.id === "string" && row.id !== "" && typeof row.label === "string";
}

/** What was stored: its trips, whether it already has the current shape with only good rows, and whether a newer build wrote it. */
function parse(raw: string): { trips: Trip[]; clean: boolean; newer: boolean } {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { trips: [], clean: false, newer: false };
  }
  // The shape before this module: a bare array of tabs.
  if (Array.isArray(data)) return { trips: data.filter(isTrip), clean: false, newer: false };
  if (typeof data === "object" && data !== null) {
    const record = data as Record<string, unknown>;
    if (typeof record.v === "number" && record.v > VERSION) {
      return { trips: Array.isArray(record.trips) ? record.trips.filter(isTrip) : [], clean: true, newer: true };
    }
    if (record.v === VERSION && Array.isArray(record.trips)) {
      const trips = record.trips.filter(isTrip);
      return { trips, clean: trips.length === record.trips.length, newer: false };
    }
  }
  return { trips: [], clean: false, newer: false };
}

/** Stores the tabs. When the browser will not take them, or the store is not ours to change, they are held in this window instead. */
function save(trips: Trip[]): void {
  const storage = safeStorage();
  if (storage && !heldBack) {
    try {
      storage.setItem(KEY, JSON.stringify({ v: VERSION, trips }));
      unsaved = null;
      return;
    } catch {
      // Quota or a blocked write: the tabs still work for this window, they are just not kept.
    }
  }
  unsaved = trips;
}

/** The tabs. Anything unreadable reads as `[]` and is rewritten so the next read is clean. */
export function readTrips(): Trip[] {
  const storage = safeStorage();
  let raw: string | null = null;
  // An unreadable store holds no tabs; those of this window are all there is.
  heldBack = storage === null;
  if (storage) {
    try {
      raw = storage.getItem(KEY);
    } catch {
      heldBack = true;
    }
  }
  if (raw === null) return unsaved ?? [];
  const found = parse(raw);
  heldBack = found.newer;
  if (unsaved) return unsaved;
  if (!found.clean) save(found.trips);
  return found.trips;
}

/**
 * Change the tabs: `update` gets the tabs as stored right now, and what it returns is saved and announced.
 * Returning the list it was given changes nothing and announces nothing.
 */
export function writeTrips(update: (current: Trip[]) => Trip[]): Trip[] {
  const current = readTrips();
  const next = update(current);
  if (next === current) return current;
  save(next);
  for (const listener of listeners) listener(next);
  return next;
}

/** Call `listener` with the tabs whenever any window changes them. Returns the function that stops it. */
export function subscribeTrips(listener: (trips: Trip[]) => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    // A null key means the whole storage was cleared.
    if (event.key === KEY || event.key === null) {
      // Another window's write went through, so the store takes tabs again: what it holds is the list.
      unsaved = null;
      listener(readTrips());
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}
