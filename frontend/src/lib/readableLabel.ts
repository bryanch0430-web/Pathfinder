/** Turn a machine token into words. "buffet_restaurant" becomes "Buffet restaurant".
 *  Text already written for a person ("Free Wi-Fi") is left alone. */
export function readableLabel(value: string): string {
  const text = value.trim();
  if (!text || (!/[_-]/.test(text) && /[\sA-Z]/.test(text))) return text;
  const words = text.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
