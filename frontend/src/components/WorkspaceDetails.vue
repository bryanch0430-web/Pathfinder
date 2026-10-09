<script setup lang="ts">
import { computed } from 'vue'
import type { DisruptionKind } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { AGENT_LABELS, AGENT_NAMES } from '@/lib/constants'
import { formatDate, formatMoney, formatMoneyValue, formatPercent, safeHttpUrl } from '@/lib/format'
import StalenessBadge from './StalenessBadge.vue'
import StatusBadge from './StatusBadge.vue'

/** Collapsible plan details: cost, constraint checks, reservations, disruptions and data sections. */
const planStore = usePlanStore()
const plan = computed(() => planStore.plan)

const DISRUPTION_LABELS: Record<DisruptionKind, string> = {
  venue_closed: 'Venue closed',
  weather_warning: 'Weather warning',
  transit_delay: 'Transit delay',
}

/** Human-readable names for the ids that violations and disruptions refer to. */
const labelsById = computed(() => {
  const labels = new Map<string, string>()
  for (const day of plan.value?.days ?? []) {
    for (const item of day.items ?? []) labels.set(item.item_id, item.title)
  }
  for (const t of plan.value?.tickets ?? []) labels.set(t.ticket_id, `${t.carrier} ${t.origin} to ${t.destination}`)
  return labels
})

const labelFor = (ids: string[] | undefined): string =>
  (ids ?? []).map((id) => labelsById.value.get(id) ?? id).join(', ')

const sectionFor = (agent: (typeof AGENT_NAMES)[number]) => planStore.sectionsByAgent.get(agent)

const costRows = computed(() => {
  const cost = plan.value?.cost
  if (!cost) return []
  return [
    { label: 'Hotel', amount: cost.hotel },
    { label: 'Tickets', amount: cost.tickets },
    { label: 'Attractions', amount: cost.attractions },
  ]
})

const budgetComparison = computed(() => {
  const budget = plan.value?.budget
  const cost = plan.value?.cost
  if (!budget || !cost) return null
  if (budget.currency !== cost.currency) return { comparable: false as const }
  const difference = budget.amount - cost.total
  return {
    comparable: true as const,
    over: difference < 0,
    difference: Math.abs(difference),
    ratio: budget.amount > 0 ? cost.total / budget.amount : cost.total > 0 ? Infinity : 0,
  }
})

const barWidth = computed(() => {
  const comparison = budgetComparison.value
  if (!comparison || !comparison.comparable) return '0%'
  return `${Math.min(100, Math.round(comparison.ratio * 100))}%`
})

const violations = computed(() => plan.value?.violations ?? [])
const disruptions = computed(() => plan.value?.disruptions ?? [])
const reservations = computed(() => plan.value?.reservations ?? [])
</script>

<template>
  <div v-if="plan" class="details">
    <details>
      <summary>Cost</summary>
      <p v-if="!plan.cost" class="small muted">No cost breakdown for this plan.</p>
      <div v-else class="subcard">
        <table class="cost">
          <caption class="sr-only">Cost breakdown</caption>
          <tbody>
            <tr v-for="row in costRows" :key="row.label">
              <th scope="row">{{ row.label }}</th>
              <td>{{ formatMoney(row.amount, plan.cost.currency) }}</td>
            </tr>
            <tr class="total">
              <th scope="row">Total</th>
              <td>{{ formatMoney(plan.cost.total, plan.cost.currency) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!plan.cost.complete" class="small warn-text">
          Partial total: some prices are missing, so the real cost may be higher.
        </p>
        <div v-if="plan.budget" class="budget">
          <p>
            Budget {{ formatMoneyValue(plan.budget) }}
            <template v-if="budgetComparison && budgetComparison.comparable">
              <span v-if="budgetComparison.over" class="badge badge-danger">
                Over by {{ formatMoney(budgetComparison.difference, plan.budget.currency) }}
              </span>
              <span v-else class="badge badge-ok">
                {{ formatMoney(budgetComparison.difference, plan.budget.currency) }} left
              </span>
            </template>
            <span v-else-if="budgetComparison" class="small muted">(different currency from the cost, not compared)</span>
          </p>
          <div
            v-if="budgetComparison && budgetComparison.comparable"
            class="bar"
            role="img"
            :aria-label="`Spent ${formatPercent(budgetComparison.ratio)} of the budget`"
          >
            <div class="fill" :class="{ over: budgetComparison.over }" :style="{ width: barWidth }"></div>
          </div>
        </div>
        <p v-else class="small muted">No budget set, so the cost is not compared to anything.</p>
      </div>
    </details>

    <details :open="violations.length > 0">
      <summary>
        Constraint checks
        <span v-if="violations.length > 0" class="badge badge-danger">{{ violations.length }}</span>
      </summary>
      <p v-if="violations.length === 0" class="small ok-text">No violations: dates, budget, route and tickets all passed.</p>
      <ul v-else class="list" aria-label="Violations">
        <li v-for="(violation, index) in violations" :key="index" class="subcard violation">
          <span class="badge badge-danger">{{ violation.check }}</span>
          {{ violation.message }}
          <span v-if="(violation.item_ids ?? []).length > 0" class="small muted">(affects {{ labelFor(violation.item_ids) }})</span>
        </li>
      </ul>
    </details>

    <details v-if="reservations.length > 0">
      <summary>Reservations to make <span class="badge badge-warn">{{ reservations.length }}</span></summary>
      <ul class="list">
        <li v-for="(reservation, index) in reservations" :key="index" class="subcard">
          <p>
            <strong>{{ reservation.place_name }}</strong>
            <span class="small muted"> book at least {{ reservation.lead_time_days }} days ahead</span>
          </p>
          <p v-if="safeHttpUrl(reservation.booking_url)" class="small">
            <a :href="safeHttpUrl(reservation.booking_url)!" target="_blank" rel="noopener noreferrer">Booking page</a>
          </p>
        </li>
      </ul>
    </details>

    <details v-if="disruptions.length > 0" open>
      <summary>Disruptions <span class="badge badge-warn">{{ disruptions.length }}</span></summary>
      <ul class="list">
        <li v-for="(disruption, index) in disruptions" :key="index" class="subcard">
          <div class="row">
            <span class="badge badge-warn">{{ DISRUPTION_LABELS[disruption.kind] }}</span>
            <span class="badge" :class="disruption.resolved ? 'badge-ok' : 'badge-danger'">
              {{ disruption.resolved ? 'resolved' : 'unresolved' }}
            </span>
            <span v-if="disruption.date" class="small muted">{{ formatDate(disruption.date) }}</span>
          </div>
          <p>{{ disruption.detail }}</p>
          <p v-if="(disruption.affected_ids ?? []).length > 0" class="small muted">Affects {{ labelFor(disruption.affected_ids) }}</p>
          <p v-if="disruption.resolution" class="small">Resolution: {{ disruption.resolution }}</p>
        </li>
      </ul>
    </details>

    <details>
      <summary>Data sections</summary>
      <ul class="sections">
        <li v-for="agent in AGENT_NAMES" :key="agent" class="subcard section">
          <div class="row">
            <strong>{{ AGENT_LABELS[agent] }}</strong>
            <StatusBadge v-if="sectionFor(agent)" :status="sectionFor(agent)!.status" />
            <span v-else class="badge">no data</span>
          </div>
          <p v-if="sectionFor(agent)?.status === 'unavailable'" class="small">
            The source could not be used. This part is left empty rather than filled from model knowledge.
          </p>
          <p v-else-if="sectionFor(agent)?.status === 'stale'" class="small">
            Older than the freshness limit; it is refreshed on the next change.
          </p>
          <p v-if="sectionFor(agent)?.reason" class="small">Reason: {{ sectionFor(agent)!.reason }}</p>
          <StalenessBadge :agent="agent" :fetched-at="sectionFor(agent)?.fetched_at" :missing="!sectionFor(agent)" />
        </li>
      </ul>
    </details>

    <p class="small muted">
      <template v-if="(plan.saved_trip_refs ?? []).length > 0">
        Informed by {{ plan.saved_trip_refs!.length }} similar saved
        {{ plan.saved_trip_refs!.length === 1 ? 'trip' : 'trips' }} ({{ plan.saved_trip_refs!.join(', ') }}).
      </template>
      <template v-else>No similar saved trips were used for this plan.</template>
    </p>
  </div>
</template>

<style scoped>
.details {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

details {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
}

details[open] > summary {
  margin-bottom: var(--space-2);
}

summary {
  padding: 4px 0;
  font-weight: var(--weight-semibold);
  cursor: pointer;
}

summary .badge {
  margin-left: var(--space-2);
}

.list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.sections {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-2);
}

.section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cost {
  width: 100%;
  border-collapse: collapse;
}

.cost th {
  font-weight: var(--weight-medium);
  text-align: left;
}

.cost td {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.cost th,
.cost td {
  padding: 3px 0;
}

.cost .total th,
.cost .total td {
  border-top: 1px solid var(--border-strong);
  font-weight: 700;
}

.budget {
  margin-top: 10px;
}

.bar {
  height: 10px;
  margin-top: 6px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--border);
}

.fill {
  height: 100%;
  background: var(--ok);
}

.fill.over {
  background: var(--danger);
}

.warn-text {
  margin-top: 6px;
  color: var(--warn);
}

.ok-text {
  color: var(--ok);
}

.violation {
  background: var(--danger-soft);
}
</style>
