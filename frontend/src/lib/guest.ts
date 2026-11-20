/**
 * Utility to manage anonymous guest UUIDs in localStorage.
 * Ensures consistent guest identity across page reloads and browser sessions.
 */

const GUEST_ID_STORAGE_KEY = 'tripverse_guest_id';

/**
 * Retrieves the existing guest UUID or creates, stores, and returns a new UUID v4.
 */
export function getOrCreateGuestId(): string {
  try {
    let guestId = localStorage.getItem(GUEST_ID_STORAGE_KEY);
    if (!guestId || !isValidUUID(guestId)) {
      guestId = crypto.randomUUID();
      localStorage.setItem(GUEST_ID_STORAGE_KEY, guestId);
    }
    return guestId;
  } catch (err) {
    console.warn('LocalStorage unavailable for guest ID, generating in-memory fallback:', err);
    return crypto.randomUUID();
  }
}

/**
 * Returns the currently stored guest UUID, or null if none exists.
 */
export function getStoredGuestId(): string | null {
  try {
    const guestId = localStorage.getItem(GUEST_ID_STORAGE_KEY);
    return guestId && isValidUUID(guestId) ? guestId : null;
  } catch {
    return null;
  }
}

/**
 * Clears the stored guest UUID (e.g. upon user login or explicit logout).
 */
export function clearGuestId(): void {
  try {
    localStorage.removeItem(GUEST_ID_STORAGE_KEY);
  } catch (err) {
    console.warn('Failed to clear guest ID from localStorage:', err);
  }
}

/**
 * Helper to validate standard UUID format.
 */
function isValidUUID(uuidStr: string): boolean {
  const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  return uuidRegex.test(uuidStr);
}
