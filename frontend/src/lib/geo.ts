/**
 * Client-side travel estimate between two stops. TripPlan has no travel times, so the place
 * card derives one from coordinates: great-circle distance × 1.3 detour factor, walked at
 * 4.5 km/h up to 1.5 km, otherwise transit at 20 km/h plus 8 minutes of waiting. It is labelled
 * as an estimate wherever it is shown.
 */
import type { GeoPoint } from '@/api'

const EARTH_RADIUS_KM = 6371
const DETOUR = 1.3
const WALK_LIMIT_KM = 1.5
const WALK_KMH = 4.5
const TRANSIT_KMH = 20
const TRANSIT_WAIT_MIN = 8

export function haversineKm(a: GeoPoint, b: GeoPoint): number {
  const rad = (deg: number): number => (deg * Math.PI) / 180
  const dLat = rad(b.lat - a.lat)
  const dLng = rad(b.lng - a.lng)
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

export interface TravelEstimate {
  km: number
  minutes: number
  mode: 'walk' | 'transit'
}

export function estimateTravel(from: GeoPoint, to: GeoPoint): TravelEstimate {
  const km = haversineKm(from, to) * DETOUR
  if (km <= WALK_LIMIT_KM) return { km, minutes: Math.max(1, Math.round((km / WALK_KMH) * 60)), mode: 'walk' }
  return { km, minutes: Math.round((km / TRANSIT_KMH) * 60 + TRANSIT_WAIT_MIN), mode: 'transit' }
}
