// Expansion model shared by the graph, the map and every detail layout.
// A node expands into its nearby places; a leg expands into waypoints along its path.
// Every figure carries its source so estimates are never shown as quotes.
import { airDistanceKm, type Coordinates, type ItineraryEdge, type ItineraryNode, type NearbyPlace, type RoadMetric } from './itineraryGraph';

export const dayLabel = (node: ItineraryNode) => node.kind === 'origin' ? 'Departure'
  : node.day_start ? `Day ${node.day_start}${node.day_end && node.day_end !== node.day_start ? `–${node.day_end}` : ''}` : 'Stop';

export interface Prefs { pace?: string; interests?: string[]; avoid?: string[]; start_date?: string | null }

/** Calendar month (1–12) a stop falls in, from the trip's start date; null when either is unknown. */
export function stopMonth(node: ItineraryNode, startDate?: string | null): number | null {
  if (!startDate || !node.day_start) return null;
  const date = new Date(`${startDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) return null;
  date.setDate(date.getDate() + node.day_start - 1);
  return date.getMonth() + 1;
}
export interface Figure { value: string; source: string; estimated?: boolean }
export interface FitNote { tone: 'good' | 'warn' | 'info'; text: string }
export interface LegFacts {
  mode: string; kind: 'road' | 'air' | 'rail' | 'water' | 'foot' | 'other';
  km: number | null; minutes: number | null;
  distance: Figure; duration: Figure; cost: Figure; tolls: Figure; fit: FitNote[];
}
export interface SubStop { id: string; name: string; role: 'depart' | 'waypoint' | 'arrive'; point: Coordinates | null; at: string }
export interface SubLeg { id: string; from: string; to: string; km: number | null; distance: string; duration: string; cost: string }
export interface LegExpansion { stops: SubStop[]; legs: SubLeg[] }
export interface PlaceLink { km: number | null; distance: Figure; duration: Figure; cost: Figure; fit: FitNote[] }

const KIND: [LegFacts['kind'], RegExp][] = [
  ['air', /flight|fly|plane|air/i], ['rail', /rail|train|shinkansen|metro|subway|tram/i],
  ['water', /ferry|boat|cruise/i], ['foot', /walk|hike|cycle|bike/i], ['road', /car|taxi|drive|road|bus|coach|cab/i],
];
const SPEED: Record<LegFacts['kind'], number> = { road: 55, air: 750, rail: 90, water: 30, foot: 4.8, other: 50 };

export function legKind(mode?: string | null): LegFacts['kind'] {
  return KIND.find(([, pattern]) => pattern.test(mode || ''))?.[0] || (mode ? 'other' : 'road');
}
export function parseMinutes(text?: string | null): number | null {
  if (!text) return null;
  const hours = text.match(/(\d+(?:\.\d+)?)\s*(?:h|hr|hour)/i);
  const mins = text.match(/(\d+)\s*(?:m|min)/i);
  const total = (hours ? Number(hours[1]) * 60 : 0) + (mins ? Number(mins[1]) : 0);
  return total > 0 ? Math.round(total) : null;
}
export function parseMoney(text?: string | null): { symbol: string; amount: number } | null {
  const match = text?.match(/(₹|\$|€|£|INR|USD|EUR|GBP|JPY)\s?([\d,]+(?:\.\d+)?)/i);
  return match ? { symbol: match[1], amount: Number(match[2].replace(/,/g, '')) } : null;
}
export function formatMinutes(minutes: number | null): string {
  if (minutes === null) return '—';
  const h = Math.floor(minutes / 60); const m = Math.round(minutes % 60);
  return h ? `${h}h${m ? ` ${m}m` : ''}` : `${m} min`;
}
const money = (m: { symbol: string; amount: number }, share = 1) =>
  `${m.symbol}${m.symbol.length > 1 ? ' ' : ''}${Math.round(m.amount * share).toLocaleString()}`;

export function legFacts(edge: ItineraryEdge, road: RoadMetric | undefined, air: number | null, prefs: Prefs = {}): LegFacts {
  // ponytail: unlabelled legs over 700 km straight-line are assumed to be flights; an overland
  // trip that long would carry a mode in the itinerary.
  const kind = !edge.mode && air !== null && air > 700 ? 'air' : legKind(edge.mode);
  const measured = road && road.distance_km > 0.1 ? road : undefined;
  const km = measured?.distance_km ?? (Number(edge.distance?.match(/[\d.]+/)?.[0]) || null) ?? air;
  const quotedMin = parseMinutes(edge.duration);
  const minutes = quotedMin ?? (measured ? Math.round(measured.duration_minutes) : km !== null ? Math.round(km / SPEED[kind] * 60) : null);
  const fit: FitNote[] = [];
  const avoid = (prefs.avoid || []).map((item) => item.toLowerCase());
  const mode = edge.mode?.toLowerCase();
  if (mode && avoid.some((item) => item.includes(mode) || mode.includes(item.replace(/s$/, ''))))
    fit.push({ tone: 'warn', text: `Uses ${edge.mode}, which you asked to avoid` });
  const limit = prefs.pace === 'relaxed' ? 240 : prefs.pace === 'packed' ? 600 : 360;
  if (minutes !== null && kind !== 'air') fit.push(minutes > limit
    ? { tone: 'warn', text: `${formatMinutes(minutes)} is long for a ${prefs.pace || 'balanced'} pace — consider a stop` }
    : { tone: 'good', text: `Fits a ${prefs.pace || 'balanced'} pace` });
  if (measured?.toll_cost) fit.push({ tone: 'info', text: `Tolls on this route: ${measured.toll_cost}` });
  return {
    mode: edge.mode || 'Mode not specified', kind, km, minutes,
    distance: measured ? { value: `${measured.distance_km} km`, source: 'Road route' }
      : edge.distance ? { value: edge.distance, source: 'Quoted in itinerary' }
        : air !== null ? { value: `${air} km`, source: 'Straight line', estimated: true } : { value: '—', source: 'Stops not located' },
    duration: quotedMin !== null ? { value: edge.duration!, source: 'Quoted in itinerary' }
      : measured ? { value: formatMinutes(minutes), source: 'Driving estimate' }
        : minutes !== null ? { value: `≈ ${formatMinutes(minutes)}`, source: `Typical ${kind} speed`, estimated: true } : { value: '—', source: 'No duration' },
    cost: edge.cost ? { value: edge.cost, source: 'Quoted in itinerary' } : { value: 'Not quoted', source: 'Fare unavailable' },
    tolls: measured?.toll_cost ? { value: measured.toll_cost, source: 'Google Routes' } : { value: '—', source: kind === 'road' ? 'None reported' : 'Not a road leg' },
    fit,
  };
}

// Waypoints sit at equal fractions of the path. Road legs walk the real geometry;
// other legs interpolate the straight line. Time and cost are pro-rated and marked "≈".
export function expandLeg(edge: ItineraryEdge, facts: LegFacts, names: [string, string],
  from?: Coordinates, to?: Coordinates, road?: RoadMetric, parts = 3): LegExpansion {
  const path: Coordinates[] = road?.geometry?.length ? road.geometry.map(([lon, lat]) => ({ lat, lon }))
    : from && to ? [from, to] : [];
  const cumulative = path.reduce<number[]>((acc, point, i) => [...acc, i ? acc[i - 1] + airDistanceKm(path[i - 1], point) : 0], []);
  const pathKm = cumulative.at(-1) || 0;
  const pointAt = (fraction: number): Coordinates | null => {
    if (!path.length) return null;
    if (path.length === 2 || !pathKm) return { lat: path[0].lat + (path[1].lat - path[0].lat) * fraction, lon: path[0].lon + (path[1].lon - path[0].lon) * fraction };
    const target = pathKm * fraction;
    return path[Math.max(0, cumulative.findIndex((km) => km >= target))];
  };
  const fee = parseMoney(facts.cost.value);
  const stops: SubStop[] = Array.from({ length: parts + 1 }, (_, i) => {
    const f = i / parts;
    return {
      id: `${edge.id}~${i}`, role: i === 0 ? 'depart' : i === parts ? 'arrive' : 'waypoint',
      name: i === 0 ? names[0] : i === parts ? names[1] : `${facts.kind === 'air' ? 'In flight' : 'En route'} · ${Math.round(f * 100)}%`,
      point: i === 0 ? from || null : i === parts ? to || null : pointAt(f),
      at: facts.km !== null ? `km ${Math.round(facts.km * f)}${facts.minutes !== null ? ` · +${formatMinutes(Math.round(facts.minutes * f))}` : ''}` : `${Math.round(f * 100)}% of leg`,
    };
  });
  const share = 1 / parts;
  return {
    stops,
    legs: stops.slice(1).map((stop, i) => ({
      id: `${edge.id}~${i}-${i + 1}`, from: stops[i].id, to: stop.id,
      km: facts.km !== null ? Math.round(facts.km * share) : null,
      distance: facts.km !== null ? `≈ ${Math.round(facts.km * share)} km` : '—',
      duration: facts.minutes !== null ? `≈ ${formatMinutes(Math.round(facts.minutes * share))}` : '—',
      cost: fee ? `≈ ${money(fee, share)}` : '—',
    })),
  };
}

export function placeLink(place: NearbyPlace, prefs: Prefs = {}): PlaceLink {
  const km = place.distance_km ?? null;
  const walk = km !== null && km <= 1.5;
  const minutes = km !== null ? Math.max(1, Math.round(km / (walk ? 4.8 : 25) * 60)) : null;
  const interests = (prefs.interests || []).map((item) => item.toLowerCase());
  const text = `${place.category} ${place.name}`.toLowerCase();
  const matched = interests.find((item) => text.includes(item) || item.split(/\s+/).some((word) => word.length > 3 && text.includes(word)));
  return {
    km,
    distance: km !== null ? { value: `${km} km`, source: 'Straight line' } : { value: '—', source: 'Mentioned in draft' },
    duration: minutes !== null ? { value: `≈ ${formatMinutes(minutes)}`, source: walk ? 'Walking' : 'Short drive', estimated: true } : { value: '—', source: 'Not measured' },
    cost: place.price_level ? { value: place.price_level, source: 'Google price level' }
      : parseMoney(place.evidence) ? { value: money(parseMoney(place.evidence)!), source: 'Quoted in itinerary' } : { value: 'Not quoted', source: 'Check before visiting' },
    fit: matched ? [{ tone: 'good', text: `Matches your interest in ${matched}` }] : [],
  };
}
