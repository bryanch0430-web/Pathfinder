/**
 * Guard for every link target that comes from data.
 *
 * Web results, LLM output and provider responses all carry URLs, and the app
 * renders them as `href`s or opens them in a new window. A `javascript:` or
 * `data:` URL there would run script in the app's origin on click, so any
 * non-constant link goes through `safeHref()` first.
 *
 * The check parses the value with the browser's own URL parser, so whitespace,
 * tabs or mixed case cannot hide the scheme. Relative paths resolve against the
 * page and pass, since they stay on the app's own http(s) origin. The app links
 * nothing over `mailto:` or `tel:`, so those are refused too.
 */

const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

function baseUrl(): string {
  try {
    return window.location.href;
  } catch {
    // No window (a test runner or a pre-render): any http(s) base will do.
    return "http://localhost/";
  }
}

/** The URL itself when it is http(s), otherwise `undefined`; render plain text then. */
export function safeHref(url?: string | null): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url, baseUrl());
    return ALLOWED_PROTOCOLS.has(parsed.protocol) ? url : undefined;
  } catch {
    return undefined;
  }
}
