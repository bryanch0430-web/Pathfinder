import type { TripContext } from '@/api'

export type KeyVariable = 'destination' | 'dates' | 'party_size' | 'budget'

export const KEY_VARIABLE_LABELS: Record<KeyVariable, string> = {
  destination: 'Destination',
  dates: 'Dates (start date plus end date or number of days)',
  party_size: 'Party size',
  budget: 'Budget',
}

/**
 * The key variables the backend needs before it can plan. When any is missing the planner asks a
 * clarification question instead of guessing, so the UI surfaces the same list up front.
 */
export function missingKeyVariables(context: TripContext): KeyVariable[] {
  const missing: KeyVariable[] = []
  if (!context.destination?.trim()) missing.push('destination')
  if (!context.start_date || !(context.end_date || context.days)) missing.push('dates')
  if (!context.party_size) missing.push('party_size')
  if (!context.budget || !(context.budget.amount > 0)) missing.push('budget')
  return missing
}
