/** Turn a machine token into words. "buffet_restaurant" becomes "Buffet restaurant".
 *  Text already written for a person ("Free Wi-Fi", "5-star", "step-free") is
 *  left alone, hyphen and capitals included. */
export function readableLabel(value: string): string {
  const text = value.trim();
  if (!text) return text;
  // Underscored lowercase tokens are machine labels; so is a lone lowercase word.
  if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/.test(text)) {
    const words = text.replace(/_+/g, " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  if (/^[a-z]+$/.test(text)) return text.charAt(0).toUpperCase() + text.slice(1);
  return text;
}
