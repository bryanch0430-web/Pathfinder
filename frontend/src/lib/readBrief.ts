import type { OnboardingValues } from '../components/create/TripOnboardingForm';

/** Facts already written in the chat. A day count is never a party size. */

export interface ChatBrief {
  origin?: string;
  destination?: string;
  places: string[];
  days?: number;
  budget?: number;
  currency?: string;
  interests: string[];
  avoid: string[];
  adults?: number;
  children?: number;
  comfort?: OnboardingValues['planning_preferences']['comfort'];
  pace?: OnboardingValues['planning_preferences']['pace'];
  travelMode?: OnboardingValues['planning_preferences']['travel_mode'];
}

const SYMBOLS: [string, string][] = [
  ['HK$', 'HKD'], ['US$', 'USD'], ['A$', 'AUD'], ['C$', 'CAD'], ['S$', 'SGD'], ['NT$', 'TWD'],
  ['$', 'USD'], ['£', 'GBP'], ['€', 'EUR'], ['¥', 'JPY'],
];

function unique(items: string[], limit: number): string[] {
  const seen: string[] = [];
  for (const item of items) {
    const cleaned = item.replace(/\s+/g, ' ').trim().replace(/^[.\s]+|[.\s]+$/g, '');
    if (cleaned.length < 2 || cleaned.length > 80) continue;
    if (seen.some((kept) => kept.toLowerCase() === cleaned.toLowerCase())) continue;
    seen.push(cleaned);
    if (seen.length === limit) break;
  }
  return seen;
}

function firstGroup(match: RegExpMatchArray | null): string {
  return match?.slice(1).find(Boolean)?.trim() || '';
}

export function readBrief(text: string): ChatBrief {
  const source = text || '';
  const brief: ChatBrief = { places: [], interests: [], avoid: [] };
  const days = source.match(/\b(\d{1,3})\s*-\s*days?\b|\b(\d{1,3})\s+days?\b/i);
  if (days) brief.days = Number(firstGroup(days));
  const destination = source.match(/\bto\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b/);
  if (destination) brief.destination = destination[1].trim();
  const origin = source.match(/\bfrom\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b/);
  if (origin) brief.origin = origin[1].trim();
  const places = [...source.matchAll(/\b(?:in|at)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b/g)].map((match) => match[1].trim());
  const focus = source.match(/\b(?:focus(?:ing|ed)? on|interested in|including)\s+(.+?)(?:\s+with\s+(?:a\s+)?budget\b|[.]|$)/i);
  const interests: string[] = [];
  if (focus) {
    for (const chunk of focus[1].split(/,|\band\b/i)) {
      const place = chunk.match(/\b(?:in|at)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b/);
      if (place) places.push(place[1].trim());
      const interest = chunk.replace(/\b(?:in|at)\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\b/g, '').trim();
      if (interest) interests.push(interest);
    }
  }
  brief.places = unique(places.filter((place) => place.toLowerCase() !== brief.destination?.toLowerCase()), 20);
  brief.interests = unique(interests, 6);
  brief.avoid = unique([...source.matchAll(/\b(?:avoid|no|without)\s+([^,.]{2,80})/gi)].map((match) => match[1].trim()), 6);
  const money = source.match(/(HK\$|US\$|A\$|C\$|S\$|NT\$|\$|£|€|¥)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|\bbudget(?:\s+of)?\s+(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([A-Z]{3})\b/i);
  if (money) {
    const amount = Number((money[2] || money[3] || '').replace(/,/g, ''));
    if (amount > 0) {
      brief.budget = amount;
      brief.currency = SYMBOLS.find(([symbol]) => symbol === money[1])?.[1] || money[4]?.toUpperCase();
    }
  }
  const adults = source.match(/\b(\d{1,2})\s+adults?\b|\bfor\s+(\d{1,2})\s+(?:people|travelers|travellers)\b/i);
  if (adults) brief.adults = Number(firstGroup(adults));
  const children = source.match(/\b(\d{1,2})\s+(?:children|kids|child)\b/i);
  if (children) brief.children = Number(children[1]);
  if (/\bmid[-\s]?range\b/i.test(source)) brief.comfort = 'mid_range';
  else if (/\b(?:comfortable|luxury|nicer stays)\b/i.test(source)) brief.comfort = 'comfortable';
  else if (/\b(?:on a budget|budget travel|hostels)\b/i.test(source)) brief.comfort = 'budget';
  if (/\brelaxed\b/i.test(source)) brief.pace = 'relaxed';
  else if (/\b(?:packed|full days)\b/i.test(source)) brief.pace = 'packed';
  if (/\b(?:walk|walking)\b/i.test(source)) brief.travelMode = 'walk';
  else if (/\b(?:taxi|rideshare)\b/i.test(source)) brief.travelMode = 'taxi';
  else if (/\b(?:drive|rental car|driving)\b/i.test(source)) brief.travelMode = 'drive';
  else if (/\b(?:public transit|transit|train|metro)\b/i.test(source)) brief.travelMode = 'transit';
  return brief;
}

/** The form's starting values: what the trip already stored, then whatever the chat stated. */
export function prefillFromChat(initial: Partial<OnboardingValues> | null | undefined, text: string): Partial<OnboardingValues> {
  const brief = readBrief(text);
  const prefs = { ...(initial?.planning_preferences || {}) };
  const days = initial?.duration_days || brief.days;
  if (prefs.adults === days && brief.adults == null && brief.days === days) delete prefs.adults;
  const places = unique([...(initial?.places_to_visit || []), ...brief.places], 20);
  const interests = unique([...(prefs.interests || []), ...brief.interests], 6);
  const avoid = unique([...(prefs.avoid || []), ...brief.avoid], 6);
  return {
    ...initial,
    origin: initial?.origin || brief.origin || '',
    destination: initial?.destination || brief.destination || '',
    duration_days: days,
    places_to_visit: places,
    budget_amount: initial?.budget_amount || brief.budget || null,
    currency: brief.currency || initial?.currency || 'HKD',
    planning_preferences: {
      pace: brief.pace || prefs.pace || 'balanced',
      interests,
      avoid,
      start_date: prefs.start_date,
      adults: brief.adults ?? prefs.adults,
      children: brief.children ?? prefs.children,
      comfort: brief.comfort || prefs.comfort,
      travel_mode: brief.travelMode || prefs.travel_mode,
      guide: prefs.guide,
      guide_shapes_plan: prefs.guide_shapes_plan !== false,
      home_currency: prefs.home_currency,
    },
  };
}
