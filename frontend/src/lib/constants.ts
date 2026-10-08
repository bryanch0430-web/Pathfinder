import type { AgentName, ConstraintKind, Route } from '@/api'

/** The four pre-planning agents, in display order. `satisfies` keeps this in sync with the contract. */
export const AGENT_NAMES = ['attraction', 'hotel', 'weather', 'ticket'] as const satisfies readonly AgentName[]

export const AGENT_LABELS: Record<AgentName, string> = {
  attraction: 'Attractions',
  hotel: 'Hotel',
  weather: 'Weather',
  ticket: 'Tickets',
}

export const ROUTE_LABELS: Record<Route, string> = {
  plan: 'Plan',
  modify: 'Modify',
  ask: 'Ask',
  unclear: 'Unclear',
}

export const CONSTRAINT_KINDS: ReadonlyArray<{ value: ConstraintKind; label: string; hint: string }> = [
  { value: 'budget', label: 'Budget', hint: 'e.g. under 150000 JPY in total' },
  { value: 'must_visit', label: 'Must visit', hint: 'e.g. Fushimi Inari' },
  { value: 'avoid', label: 'Avoid', hint: 'e.g. crowded theme parks' },
  { value: 'dates', label: 'Dates', hint: 'e.g. not before 12 April' },
  { value: 'party', label: 'Party', hint: 'e.g. one traveller uses a wheelchair' },
  { value: 'free_text', label: 'Other', hint: 'anything else the plan must respect' },
]

export const CONSTRAINT_LABELS: Record<ConstraintKind, string> = Object.fromEntries(
  CONSTRAINT_KINDS.map((kind) => [kind.value, kind.label]),
) as Record<ConstraintKind, string>

/** "any" is represented as an unset hotel_style in the API. */
export const HOTEL_STYLES = ['any', 'budget', 'business', 'boutique', 'luxury', 'ryokan'] as const

export const DEFAULT_CURRENCY = 'JPY'

export const EXAMPLE_PROMPTS = [
  'Plan my trip',
  'Swap the hotel for something cheaper',
  'What is the weather in Kyoto on 12 April?',
] as const

export const MAX_MESSAGE_LENGTH = 4000
