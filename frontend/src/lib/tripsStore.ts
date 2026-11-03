/**
 * The open trip tabs, shared by every window of the browser.
 *
 * They live in `pathfinder.trips` as `{ v: 1, trips: [{ id, label }] }`. Two things
 * used to go wrong with a bare array read and written from component state:
 *
 * - A damaged value (not JSON, or JSON of the wrong shape) broke the tab bar, or came back
 *   on every load. Here an unreadable value reads as no tabs and is written back clean, so
 *   the next read is fine. A bare array from an earlier build is carried over once.
 * - A second window kept its own copy and wrote it over the first one's tabs. Here every
 *   write starts from what is stored right now, and the `storage` event tells the other
 *   windows to read it again.
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

/** What was stored: its trips, and whether it already has the current shape with only good rows. */
function parse(raw: string): { trips: Trip[]; clean: boolean } {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { trips: [], clean: false };
  }
  // The shape before this module: a bare array of tabs.
  if (Array.isArray(data)) return { trips: data.filter(isTrip), clean: false };
  if (typeof data === "object" && data !== null) {
    const record = data as Record<string, unknown>;
    if (record.v === VERSION && Array.isArray(record.trips)) {
      const trips = record.trips.filter(isTrip);
      return { trips, clean: trips.length === record.trips.length };
    }
  }
  return { trips: [], clean: false };
}

function save(trips: Trip[]): void {
  try {
    safeStorage()?.setItem(KEY, JSON.stringify({ v: VERSION, trips }));
  } catch {
    // Quota or a blocked write: the tabs still work for this window, they are just not kept.
  }
}

/** The saved tabs. Anything unreadable reads as `[]` and is rewritten so the next read is clean. */
export function readTrips(): Trip[] {
  const storage = safeStorage();
  if (!storage) return [];
  let raw: string | null;
  try {
    raw = storage.getItem(KEY);
  } catch {
    return [];
  }
  if (raw === null) return [];
  const found = parse(raw);
  if (!found.clean) save(found.trips);
  return found.trips;
}

/** Change the tabs: `update` gets the tabs as stored right now, and what it returns is saved and announced. */
export function writeTrips(update: (current: Trip[]) => Trip[]): Trip[] {
  const next = update(readTrips());
  save(next);
  for (const listener of listeners) listener(next);
  return next;
}

/** Call `listener` with the tabs whenever any window changes them. Returns the function that stops it. */
export function subscribeTrips(listener: (trips: Trip[]) => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    // A null key means the whole storage was cleared.
    if (event.key === KEY || event.key === null) listener(readTrips());
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}
