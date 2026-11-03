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
