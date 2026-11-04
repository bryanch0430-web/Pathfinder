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
 *
 * Every read and write of these values goes through `readStoredValue()` and
 * `writeStoredValue()`. Some browsers block storage altogether (cookies off,
 * a sandboxed frame) or refuse writes once it is full; a value the browser
 * will not keep is held in the page instead, so the guest token, the sign-in
 * and the open trip still work until the page is closed.
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
  "developer", // "1" shows each model step in the chat
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

/** Values the browser would not keep, held for the life of this page. */
const held = new Map<string, string>();

/** A stored value, or what this page holds in its place; null when there is none. Never throws. */
export function readStoredValue(name: StorageKey): string | null {
  const key = storageKey(name);
  try {
    const value = safeLocalStorage()?.getItem(key);
    if (value !== null && value !== undefined) return value;
  } catch {
    // Unreadable: what this page holds is all there is.
  }
  return held.get(key) ?? null;
}

/** Store a value; when the browser will not keep it, this page holds it instead. Never throws. */
export function writeStoredValue(name: StorageKey, value: string): void {
  const key = storageKey(name);
  try {
    const storage = safeLocalStorage();
    if (storage) {
      storage.setItem(key, value);
      held.delete(key);
      return;
    }
  } catch {
    // Quota or a blocked write: held in the page below.
  }
  held.set(key, value);
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
  for (const key of [`${STORAGE_PREFIX}.${name}`, `${LEGACY_PREFIX}.${name}`]) {
    held.delete(key);
    try {
      storage?.removeItem(key);
    } catch {
      // A blocked write: nothing more we can do for this key.
    }
  }
}

/** Told when the sign-in is dropped here, so what the page shows for the account can be dropped with it. */
const identityListeners = new Set<() => void>();

/**
 * Call `listener` whenever the sign-in changes in this browser: dropped in this window, or made or dropped in another
 * (the browser only fires `storage` in the other windows). Returns the function that stops it.
 */
export function subscribeIdentity(listener: () => void): () => void {
  identityListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    // A null key means the whole storage was cleared.
    if (event.key === null || event.key === storageKey("auth")) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    identityListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/**
 * Forget the account's sign-in in this browser and go back to the guest token. A browser with no guest token
 * to go back to drops the `user:` profile token too, so the next trip starts with a fresh guest token
 * instead of one the server refuses without its bearer.
 */
export function forgetSignIn(): void {
  clearStoredValue("auth");
  const guest = readStoredValue("guest");
  if (guest) writeStoredValue("client", guest);
  else if (readStoredValue("client")?.startsWith("user:")) clearStoredValue("client");
  for (const listener of identityListeners) listener();
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
