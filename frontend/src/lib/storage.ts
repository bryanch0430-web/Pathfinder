/**
 * Browser storage keys, plus a one-time migration from the old prefix.
 *
 * The product used to be called "Pathfinder 30" and the console persisted its
 * guest token, trip tabs, session pointer, auth token, admin token and locale
 * under `pathfinder30.*`. The name is now just Pathfinder, so keys live under
 * `pathfinder.*`.
 *
 * Renaming a key on its own would strand every existing browser: the app would
 * look for `pathfinder.session`, find nothing, and start a fresh chat over the
 * traveller's saved trips. So `migrateLegacyStorage()` copies each old value to
 * its new key at boot, and never overwrites a key that already exists.
 *
 * The migration runs once per browser and then deletes the legacy keys. An
 * earlier version left them in place and copied on every load, which meant a
 * value the user had cleared came back: signing out removed `pathfinder.auth`,
 * and the next load copied `pathfinder30.auth` over it, signing them back in.
 * Now a `pathfinder.migrated` marker stops any second run, the legacy keys are
 * removed after the copy, and `clearStoredValue()` removes a value under both
 * prefixes, so a cleared value stays cleared even if one of those steps fails.
 * The cost is that a tab still running the pre-rename build loses its state on
 * its next load; that build is long gone, so this is the right trade.
 */

export const STORAGE_PREFIX = "pathfinder";
export const LEGACY_PREFIX = "pathfinder30";

/** Set once the legacy keys have been carried over; its presence skips the migration. */
export const MIGRATION_MARKER = `${STORAGE_PREFIX}.migrated`;

/** Every key the console persists, without the prefix. */
export const STORAGE_KEYS = [
  "client", // guest profile token
  "session", // active session pointer
  "trips", // open tab labels: { v: 1, trips: [{ id, label }] } (see tripsStore.ts)
  "auth", // bearer token for a signed-in account
  "guest", // guest token remembered across a sign-in
  "admin", // local admin token for the evaluation sandbox
  "locale", // EN / 简 / 繁
] as const;

export type StorageKey = (typeof STORAGE_KEYS)[number];

/** Fully-qualified key for `localStorage.getItem` / `setItem`. */
export function storageKey(name: StorageKey): string {
  return `${STORAGE_PREFIX}.${name}`;
}

function safeLocalStorage(): Storage | null {
  try {
    // Accessing localStorage throws in some privacy modes and sandboxed frames.
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Remove a value under both the current and the legacy prefix.
 *
 * Use this, not `localStorage.removeItem`, whenever the user clears something
 * (signing out, a refused token, a dropped session), so no copy of it is left
 * for a migration to bring back.
 */
export function clearStoredValue(name: StorageKey): void {
  const storage = safeLocalStorage();
  if (!storage) return;
  for (const key of [`${STORAGE_PREFIX}.${name}`, `${LEGACY_PREFIX}.${name}`]) {
    try {
      storage.removeItem(key);
    } catch {
      // A blocked write: nothing more we can do for this key.
    }
  }
}

/**
 * Copy any legacy `pathfinder30.*` value to its `pathfinder.*` key, once.
 *
 * Returns the names that were migrated, which is useful in tests and in the
 * debug console. After the first run it returns `[]` without touching storage.
 * Safe to call when storage is unavailable.
 */
export function migrateLegacyStorage(): StorageKey[] {
  const storage = safeLocalStorage();
  if (!storage) return [];

  try {
    if (storage.getItem(MIGRATION_MARKER) !== null) return [];
  } catch {
    return [];
  }

  const migrated: StorageKey[] = [];
  for (const name of STORAGE_KEYS) {
    const next = `${STORAGE_PREFIX}.${name}`;
    const previous = `${LEGACY_PREFIX}.${name}`;
    try {
      const value = storage.getItem(previous);
      if (value === null) continue;
      if (storage.getItem(next) === null) {
        storage.setItem(next, value);
        migrated.push(name);
      }
      storage.removeItem(previous);
    } catch {
      // Quota or a blocked write: leave the legacy key alone and carry on.
    }
  }
  try {
    storage.setItem(MIGRATION_MARKER, "1");
  } catch {
    // Without the marker the next load runs again; the legacy keys it removed
    // are already gone, so it cannot bring a cleared value back.
  }
  return migrated;
}
