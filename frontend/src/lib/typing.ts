/**
 * Text typed through an input method (Chinese, Japanese, Korean).
 *
 * The Enter that picks a candidate word must only confirm the word. Chromium and Firefox send that
 * Enter as a keydown with `isComposing` true and end the composition after it. WebKit (Safari, and
 * every browser on iOS) ends the composition first, so the same Enter arrives with `isComposing`
 * false and the legacy key code 229. Both are checked, so the Enter does not send a message, sign
 * in or add a tag in either order.
 */
export function isImeKey(event: { nativeEvent: KeyboardEvent }): boolean {
  const key = event.nativeEvent;
  return key.isComposing || key.keyCode === 229;
}

/** What separates the entries of a list typed in one field: "," from a keyboard, "，" and "、" from a Chinese or Japanese input method. */
const LIST_SEPARATORS = /[,，、]/;

/** The entries in `text`, trimmed and without the empty ones. */
export function splitList(text: string): string[] {
  return text
    .split(LIST_SEPARATORS)
    .map((value) => value.trim())
    .filter(Boolean);
}

/**
 * `current` followed by the tags in `text`, without repeats. Text typed or pasted into a tag field may hold
 * several tags: "," from a keyboard and "，" or "、" from a Chinese or Japanese input method all separate them.
 * A tag is cut to `maxLength` characters, and no more than `limit` tags are kept in all.
 */
export function addTags(current: string[], text: string, limit = Infinity, maxLength = Infinity): string[] {
  const next = [...current];
  for (const tag of text.split(LIST_SEPARATORS)) {
    const clean = Array.from(tag.trim()).slice(0, maxLength).join("").trim();
    if (clean && !next.includes(clean) && next.length < limit) next.push(clean);
  }
  return next;
}
