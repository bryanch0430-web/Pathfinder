export interface ItineraryNode {
  id: string;
  name: string;
  kind: 'origin' | 'stop';
  day_start: number | null;
  day_end: number | null;
  evidence: string | null;
  nearby_places?: { name: string; category: string; evidence: string }[];
}

export interface ItineraryEdge {
  id: string;
  source: string;
  target: string;
  mode: string | null;
  duration: string | null;
  cost: string | null;
  distance?: string | null;
}

export interface ItineraryGraph {
  version: number;
  nodes: ItineraryNode[];
  edges: ItineraryEdge[];
}

export interface Coordinates { lat: number; lon: number }

export interface RoadMetric {
  id: string;
  distance_km: number;
  duration_minutes: number;
  geometry: [number, number][];
  toll_cost?: string | null;
}

export interface NearbyPlace {
  id: string;
  name: string;
  category: string;
  lat?: number;
  lon?: number;
  distance_km?: number;
  price_level?: string | null;
  maps_url?: string | null;
  evidence?: string | null;
  source: 'itinerary' | 'google';
}

export function airDistanceKm(a: Coordinates, b: Coordinates): number {
  const toRad = (degrees: number) => degrees * Math.PI / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const arc = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return Math.round(6371 * 2 * Math.asin(Math.sqrt(arc)));
}

// Older saved plans predate the structured payload. Use only place names found in the text.
export function graphFromLegacyText(
  text: string, origin: string | null | undefined,
  destination: string, requestedPlaces: string[],
): ItineraryGraph {
  const known = [...new Set([...requestedPlaces, destination].filter(Boolean))];
  const names = known.filter((name) => text.toLowerCase().includes(name.toLowerCase()))
    .sort((a, b) => text.toLowerCase().indexOf(a.toLowerCase()) - text.toLowerCase().indexOf(b.toLowerCase()));
  if (!names.length) names.push(destination);
  const nodes: ItineraryNode[] = [
    ...(origin ? [{ id: 'origin', name: origin, kind: 'origin' as const, day_start: null, day_end: null,
      evidence: 'Departure point supplied by the traveler' }] : []),
    ...names.map((name, index) => ({
      id: `stop-${index + 1}`, name, kind: 'stop' as const, day_start: null, day_end: null,
      evidence: text.split('\n').find((line) => line.toLowerCase().includes(name.toLowerCase()))?.trim() || null,
    })),
  ];
  return {
    version: 1,
    nodes,
    edges: nodes.slice(1).map((node, index) => ({
      id: `leg-${index + 1}`, source: nodes[index].id, target: node.id,
      mode: null, duration: null, cost: null,
    })),
  };
}
