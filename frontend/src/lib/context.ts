import type { TripContext } from '@/api'
import { formatMoney, formatShortDate } from './format'

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

/** "10–12 Nov" (same month), "30 Nov – 2 Dec", "10 Nov · 3 days" or "" from the context's dates. */
function datesSummary(context: TripContext): string {
  const start = context.start_date
  const end = context.end_date
  if (start && end) {
    if (start.slice(0, 7) === end.slice(0, 7)) return `${Number(start.slice(8, 10))}–${formatShortDate(end)}`
    return `${formatShortDate(start)} – ${formatShortDate(end)}`
  }
  const days = context.days ? `${context.days} ${context.days === 1 ? 'day' : 'days'}` : ''
  return [start ? formatShortDate(start) : '', days].filter(Boolean).join(' · ')
}

/** One-line trip summary for the collapsed input panel, e.g. "Kyoto · 10–12 Nov · 2 people · US$1,500.00". */
export function contextSummary(context: TripContext): string {
  const parts = [
    context.destination?.trim() ?? '',
    datesSummary(context),
    context.party_size ? `${context.party_size} ${context.party_size === 1 ? 'person' : 'people'}` : '',
    context.budget ? formatMoney(context.budget.amount, context.budget.currency) : '',
  ]
  return parts.filter(Boolean).join(' · ')
}
