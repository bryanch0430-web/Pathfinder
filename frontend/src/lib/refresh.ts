/**
 * Chat messages that send one section back through the modify path. The API has no "refresh
 * section" endpoint, so a stale or unavailable fact is refreshed by asking for it in chat; the
 * modify path then re-runs only that agent (DECISIONS.md, frontend section).
 */
import type { AgentName } from '@/api'

export const REFRESH_MESSAGES: Record<AgentName, string> = {
  weather: 're-check the weather',
  hotel: 're-check the hotel',
  ticket: 're-check the train and flight tickets',
  attraction: 're-check whether any attraction is closed',
}

export const OPTIMISE_BUDGET_MESSAGE = 'reduce budget'

/**
 * "Change the order of the day 2 stops to: Nijo Castle, then Gion District, then ...".
 * Worded as a change so the router takes the modify path, and without a calendar date so the
 * change extractor cannot mistake it for a date change.
 */
export function reorderMessage(dayNumber: number, titles: readonly string[]): string {
  return `Change the order of the day ${dayNumber} stops to: ${titles.join(', then ')}`
}
