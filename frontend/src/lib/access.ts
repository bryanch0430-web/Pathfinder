/**
 * Local admin mode, decided by the backend (`local_admin` in /api/ready, true only for a caller on the owner's
 * own machine with LOCAL_ADMIN set in development). In that mode the admin areas show without a stored admin
 * token and the page offers no sign-in, registration or sign-out. Pure, so `node --test` can load it.
 */
import type { MessageKey } from "./i18n";

/** `local_admin` from the /api/ready body: only a literal `true` turns the mode on. */
export function localAdminFrom(ready: unknown): boolean {
  return typeof ready === "object" && ready !== null && (ready as { local_admin?: unknown }).local_admin === true;
}

/** Whether the admin areas (evaluation, the server key card, the threat panel) show. */
export function showsAdmin(localAdmin: boolean, storedAdminToken: string | null | undefined): boolean {
  return localAdmin || Boolean(storedAdminToken);
}

/** Whether the page offers sign-in, registration and sign-out. */
export function offersSignIn(localAdmin: boolean): boolean {
  return !localAdmin;
}

/** In local admin mode, the words of a message that would ask the traveller to sign in. */
const WITHOUT_SIGN_IN: Partial<Record<MessageKey, MessageKey>> = {
  "errors.signInToOpen": "errors.notOpenHere",
  "errors.signInEnded": "errors.signInEndedGuest",
};

/** The message to show: in local admin mode, never one that asks for a sign-in. */
export function withoutSignIn(key: MessageKey, localAdmin: boolean): MessageKey {
  return localAdmin ? WITHOUT_SIGN_IN[key] ?? key : key;
}
