/**
 * "Popular times" is not part of the TripPlan contract (Place has no such field), so this always
 * returns null today and the chart stays hidden. It reads the field defensively so the chart
 * appears without UI changes if the contract ever adds `popular_times` to Place.
 */
import type { Place } from '@/api'

/** Weekday name ("Mon".."Sun") -> 24 hourly busyness values from 0 to 100. */
export type PopularTimes = Record<string, number[]>

export function popularTimesOf(place: Place | null | undefined): PopularTimes | null {
  if (!place) return null
  const value: unknown = (place as unknown as Record<string, unknown>).popular_times
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const entries = Object.entries(value as Record<string, unknown>).filter(
    (entry): entry is [string, number[]] =>
      Array.isArray(entry[1]) && entry[1].every((n) => typeof n === 'number'),
  )
  return entries.length > 0 ? Object.fromEntries(entries) : null
}
