/**
 * Budget card data. CostBreakdown has hotel, tickets, attractions and total only, so the legend
 * maps tickets to "Transport" and shows Food and Other as not tracked (DECISIONS.md).
 */
import type { CostBreakdown, Money } from '@/api'

export type BudgetKey = 'transport' | 'attractions' | 'food' | 'hotel' | 'other'

export interface BudgetSegment {
  key: BudgetKey
  label: string
  /** null = the plan does not track this category. */
  amount: number | null
  color: string
}

export function budgetSegments(cost: CostBreakdown | null | undefined): BudgetSegment[] {
  return [
    { key: 'transport', label: 'Transport', amount: cost ? cost.tickets : 0, color: 'var(--chart-transport)' },
    { key: 'attractions', label: 'Attractions', amount: cost ? cost.attractions : 0, color: 'var(--chart-attractions)' },
    { key: 'food', label: 'Food', amount: null, color: 'var(--chart-food)' },
    { key: 'hotel', label: 'Hotel', amount: cost ? cost.hotel : 0, color: 'var(--chart-hotel)' },
    { key: 'other', label: 'Other', amount: null, color: 'var(--chart-other)' },
  ]
}

/** Fraction of the budget used, or null when there is no budget or the currencies differ. */
export function budgetUsed(cost: CostBreakdown | null | undefined, budget: Money | null | undefined): number | null {
  if (!cost || !budget || budget.amount <= 0) return null
  if (cost.currency.toUpperCase() !== budget.currency.toUpperCase()) return null
  return cost.total / budget.amount
}
