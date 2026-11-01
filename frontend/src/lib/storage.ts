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
 * its new key once, at boot, and never overwrites a key that already exists.
 *
 * The legacy keys are left in place rather than deleted: a second tab still
 * running the previous build keeps working, and the copy is idempotent.
 */

export const STORAGE_PREFIX = "pathfinder";
export const LEGACY_PREFIX = "pathfinder30";

/** Every key the console persists, without the prefix. */
export const STORAGE_KEYS = [
  "client", // guest profile token
  "session", // active session pointer
  "trips", // open tab labels: [{ id, label }]
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
 * Copy any legacy `pathfinder30.*` value to its `pathfinder.*` key.
 *
 * Returns the names that were migrated, which is useful in tests and in the
 * debug console. Safe to call more than once, and safe to call when storage is
 * unavailable.
 */
export function migrateLegacyStorage(): StorageKey[] {
  const storage = safeLocalStorage();
  if (!storage) return [];

  const migrated: StorageKey[] = [];
  for (const name of STORAGE_KEYS) {
    const next = `${STORAGE_PREFIX}.${name}`;
    const previous = `${LEGACY_PREFIX}.${name}`;
    try {
      if (storage.getItem(next) !== null) continue;
      const value = storage.getItem(previous);
      if (value === null) continue;
      storage.setItem(next, value);
      migrated.push(name);
    } catch {
      // Quota or a blocked write: leave the legacy key alone and carry on.
    }
  }
  return migrated;
}
