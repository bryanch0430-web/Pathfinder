/** Shared map geometry: distances and travel modes. */

export interface LatLng {
  lat: number;
  lng: number;
}

export type TravelMode = "walk" | "metro" | "drive" | "flight";

export const MODE: Record<TravelMode, { label: string; color: string; icon: "walk" | "train" | "car" | "plane" }> = {
  walk: { label: "Walk", color: "#7A746B", icon: "walk" },
  metro: { label: "Metro", color: "#0B7FA6", icon: "train" },
  drive: { label: "Drive", color: "#C25A1F", icon: "car" },
  flight: { label: "Flight", color: "#E08E2B", icon: "plane" },
};

export function kmBetween(a: LatLng, b: LatLng): number {
  const dx = (a.lng - b.lng) * 111 * Math.cos((((a.lat + b.lat) / 2) * Math.PI) / 180);
  const dy = (a.lat - b.lat) * 111;
  return Math.hypot(dx, dy);
}

export function modeForDistance(km: number): TravelMode {
  if (km < 2.5) return "walk";
  if (km < 35) return "metro";
  if (km < 160) return "drive";
  return "flight";
}
