/** Words that are names, not sentences: the token lowercased, the spelling kept. */
const PROPER: Record<string, string> = {
  osrm: "OSRM",
  serpapi: "SerpAPI",
  youtube: "YouTube",
  bilibili: "Bilibili",
  google_places: "Google Places",
  place_corpus: "Place corpus",
  open_meteo: "Open-Meteo",
  wikimedia: "Wikimedia",
  wikipedia: "Wikipedia",
  llm: "LLM",
  hkid: "HKID",
  hk_phone: "HK phone",
};

/** Turn a machine token into words. "buffet_restaurant" becomes "Buffet restaurant".
 *  Text already written for a person ("Free Wi-Fi", "5-star", "step-free") is
 *  left alone, hyphen and capitals included. */
export function readableLabel(value: string): string {
  const text = value.trim();
  if (!text) return text;
  const proper = PROPER[text.toLowerCase()];
  if (proper) return proper;
  // Underscored lowercase tokens are machine labels; so is a lone lowercase word.
  if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/.test(text)) {
    const words = text.replace(/_+/g, " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  if (/^[a-z]+$/.test(text)) return text.charAt(0).toUpperCase() + text.slice(1);
  return text;
}
