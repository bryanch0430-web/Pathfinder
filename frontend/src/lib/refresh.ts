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
