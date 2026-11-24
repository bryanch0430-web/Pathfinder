/**
 * What the box can read from the message as it is typed.
 *
 * This is a display aid, never a decision: the server's turn analyst still
 * decides what the message means. It only shows the traveller that their
 * numbers and dates were noticed, so they can correct them before sending.
 */

export interface QuickFact {
  key: "days" | "party" | "budget" | "date" | "place";
  label: string;
}

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];

const PLACES = [
  "Hong Kong", "Kyoto", "Osaka", "Tokyo", "Taipei", "Singapore", "Bangkok",
  "Seoul", "Sydney", "London", "Paris", "New York", "San Francisco", "Bali",
  "Shanghai", "Beijing", "Shenzhen", "Macau", "Manila", "Kuala Lumpur",
];

const CURRENCY = /\b(HKD|JPY|TWD|USD|EUR|GBP|SGD|CNY|KRW|THB)\b/i;

/** Facts a traveller usually states first, in the order they are shown. */
export function parseQuickFacts(text: string): QuickFact[] {
  const facts: QuickFact[] = [];
  const value = (text || "").trim();
  if (!value) return facts;

  const days = value.match(/\b(\d{1,2})[\s-]*(?:day|days|nights?)\b/i);
  if (days) facts.push({ key: "days", label: `${days[1]} days` });

  const party =
    value.match(/\b(\d{1,2})\s*(?:people|persons?|travellers?|travelers?|adults?|pax)\b/i) ||
    value.match(/\b(\d{1,2})\s+of\s+us\b/i);
  if (party) facts.push({ key: "party", label: `${party[1]} travellers` });

  const budget = value.match(/\b(\d[\d,.]{2,})\s*(?:HKD|JPY|TWD|USD|EUR|GBP|SGD|CNY|KRW|THB)\b/i);
  if (budget) {
    const code = value.match(CURRENCY);
    facts.push({ key: "budget", label: `${budget[1]}${code ? ` ${code[1].toUpperCase()}` : ""}` });
  }

  const monthPattern =
    "(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)";
  const dayMonth = value.match(
    new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+${monthPattern}\\b(?:\\s+(\\d{4}))?`, "i"),
  );
  const monthOnly = MONTHS.find((name) => {
    // "may" is usually the verb ("I may go"); only a capitalised May is a date.
    if (name === "may" && /\bmay\b/.test(value) && !/\bMay\b/.test(value)) return false;
    return new RegExp(`\\b${name}\\b`, "i").test(value);
  });
  if (dayMonth) {
    const name = dayMonth[2].slice(0, 3).toLowerCase();
    const label = `${dayMonth[1]} ${name.charAt(0).toUpperCase()}${name.slice(1)}${dayMonth[3] ? ` ${dayMonth[3]}` : ""}`;
    facts.push({ key: "date", label });
  } else if (monthOnly) {
    facts.push({ key: "date", label: monthOnly[0].toUpperCase() + monthOnly.slice(1) });
  }

  const places = PLACES.filter((place) => new RegExp(`\\b${place}\\b`, "i").test(value)).slice(0, 2);
  for (const place of places) facts.push({ key: "place", label: place });

  return facts.slice(0, 6);
}
