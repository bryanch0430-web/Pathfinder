/**
 * What a failed call is told to the traveller as.
 *
 * The page shows either one of its own messages or a sentence the server wrote. A proxy's HTML error
 * page, a plain-text 500, the browser's "Failed to fetch" and a storage exception are never shown as
 * they are: they give the page's own sentence for what was being done.
 */
import { ApiError } from "./api";
import type { MessageKey, Translate } from "./i18n";

/** A message key (translated when it is drawn, so a language switch translates it too) or a sentence from the server. */
export type Failure = MessageKey | { text: string };

/**
 * The failure to show for a thrown error. `fallback` is the page's own sentence for what was being done.
 * A 401 means the traveller's sign-in no longer holds, unless the caller says what else it means.
 */
export function failureOf(cause: unknown, fallback: MessageKey, unauthorized: MessageKey = "errors.signInEnded"): Failure {
  if (cause instanceof ApiError) {
    if (cause.status === 429) return "errors.rateLimited";
    if (cause.status === 401) return unauthorized;
    return cause.detail ? { text: cause.detail } : fallback;
  }
  // fetch rejects with a TypeError when the server cannot be reached at all.
  return cause instanceof TypeError ? "errors.network" : fallback;
}

/** The words of a failure in the language of `t`. */
export function describeFailure(failure: Failure, t: Translate): string {
  return typeof failure === "string" ? t(failure) : failure.text;
}

/** `failureOf` and `describeFailure` in one step, for a caller that shows the text at once. */
export function failureText(cause: unknown, t: Translate, fallback: MessageKey, unauthorized?: MessageKey): string {
  return describeFailure(failureOf(cause, fallback, unauthorized), t);
}
