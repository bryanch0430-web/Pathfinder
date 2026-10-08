<script setup lang="ts">
import { computed } from 'vue'
import type { AgentName, DayPlan, DisruptionKind, ItineraryItem, TicketOption } from '@/api'
import { useSessionStore } from '@/stores/session'
import { usePlanStore } from '@/stores/plan'
import { AGENT_LABELS, AGENT_NAMES } from '@/lib/constants'
import {
  formatDate,
  formatDateTime,
  formatMoney,
  formatMoneyValue,
  formatPercent,
  formatTimeRange,
  nightsBetween,
  safeHttpUrl,
} from '@/lib/format'
import StatusBadge from './StatusBadge.vue'

const store = useSessionStore()
const planStore = usePlanStore()
const plan = computed(() => planStore.plan)

const DISRUPTION_LABELS: Record<DisruptionKind, string> = {
  venue_closed: 'Venue closed',
  weather_warning: 'Weather warning',
  transit_delay: 'Transit delay',
}

// ---- lookups -----------------------------------------------------------------------------------

const placesById = computed(() => new Map((plan.value?.places ?? []).map((place) => [place.place_id, place])))

function ticketLabel(ticket: TicketOption): string {
  return `${ticket.carrier} ${ticket.origin} to ${ticket.destination}`
}

/** Human-readable names for the ids that violations and disruptions refer to. */
const labelsById = computed(() => {
  const labels = new Map<string, string>()
  for (const day of plan.value?.days ?? []) {
    for (const item of day.items ?? []) labels.set(item.item_id, item.title)
  }
  for (const ticket of plan.value?.tickets ?? []) labels.set(ticket.ticket_id, ticketLabel(ticket))
  return labels
})

function labelFor(ids: string[] | undefined): string {
  return (ids ?? []).map((id) => labelsById.value.get(id) ?? id).join(', ')
}

function sectionFor(agent: AgentName) {
  return (plan.value?.sections ?? []).find((section) => section.agent === agent)
}

function categoryOf(item: ItineraryItem): string {
  return placesById.value.get(item.place_id)?.category ?? ''
}

// ---- cost vs budget ----------------------------------------------------------------------------

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

// ---- confirmation toggles ----------------------------------------------------------------------

function isItemConfirmed(itemId: string): boolean {
  return (planStore.plan?.days ?? []).some((day) =>
    (day.items ?? []).some((item) => item.item_id === itemId && item.confirmed),
  )
}

function isTicketConfirmed(ticketId: string): boolean {
  return (planStore.plan?.tickets ?? []).some((ticket) => ticket.ticket_id === ticketId && ticket.confirmed)
}

function isHotelConfirmed(): boolean {
  return planStore.plan?.hotel?.confirmed ?? false
}

/**
 * Apply a confirmation change through the store, then re-sync the checkbox with the plan: if the
 * request failed the DOM state would otherwise disagree with the data.
 */
async function toggle(
  event: Event,
  apply: (confirmed: boolean) => Promise<boolean>,
  current: () => boolean,
): Promise<void> {
  const box = event.target as HTMLInputElement
  await apply(box.checked)
  box.checked = current()
}

function dayHeading(day: DayPlan, index: number): string {
  return `Day ${index + 1}: ${formatDate(day.date)}`
}

function ticketStatusClass(ticket: TicketOption): string {
  if (ticket.status === 'cancelled') return 'badge-danger'
  if (ticket.status === 'delayed') return 'badge-warn'
  return 'badge-ok'
}

function ticketStatusText(ticket: TicketOption): string {
  if (ticket.status === 'delayed') return `delayed ${ticket.delay_minutes} min`
  return ticket.status
}

const bookingLink = safeHttpUrl
</script>

<template>
  <section class="card itinerary" aria-labelledby="itinerary-title">
    <header class="card-header">
      <h2 id="itinerary-title">Itinerary</h2>
      <span v-if="plan" class="badge badge-accent" :title="`Plan ${plan.plan_id}`">Version {{ plan.version }}</span>
    </header>

    <p v-if="!plan" class="empty">
      No plan yet. Fill in the trip details and ask Pathfinder to plan your trip.
    </p>

    <div v-else class="stack">
      <!-- Header -->
      <div class="plan-head">
        <h3 class="destination">{{ plan.destination }}</h3>
        <p class="muted">
          {{ formatDate(plan.start_date) }} to {{ formatDate(plan.end_date) }}
          <span aria-hidden="true">&middot;</span>
          {{ plan.party_size }} {{ plan.party_size === 1 ? 'traveller' : 'travellers' }}
          <template v-if="plan.origin">
            <span aria-hidden="true">&middot;</span> from {{ plan.origin }}
          </template>
        </p>
        <p v-if="plan.updated_at" class="small muted">Updated {{ formatDateTime(plan.updated_at) }}</p>
      </div>

      <!-- Section status -->
      <div>
        <h3>Data sections</h3>
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
            <p v-if="sectionFor(agent)?.fetched_at" class="small muted">
              Fetched {{ formatDateTime(sectionFor(agent)!.fetched_at) }}
            </p>
          </li>
        </ul>
      </div>

      <!-- Cost vs budget -->
      <div>
        <h3>Cost</h3>
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
              <span v-else-if="budgetComparison" class="small muted">
                (different currency from the cost, not compared)
              </span>
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
      </div>

      <!-- Violations -->
      <div>
        <h3>Constraint checks</h3>
        <p v-if="(plan.violations ?? []).length === 0" class="small ok-text">
          No violations: dates, budget, route and tickets all passed.
        </p>
        <ul v-else class="stack tight" aria-label="Violations">
          <li v-for="(violation, index) in plan.violations" :key="index" class="subcard violation">
            <span class="badge badge-danger">{{ violation.check }}</span>
            {{ violation.message }}
            <span v-if="(violation.item_ids ?? []).length > 0" class="small muted">
              (affects {{ labelFor(violation.item_ids) }})
            </span>
          </li>
        </ul>
      </div>

      <!-- Disruptions -->
      <div v-if="(plan.disruptions ?? []).length > 0">
        <h3>Disruptions</h3>
        <ul class="stack tight">
          <li v-for="(disruption, index) in plan.disruptions" :key="index" class="subcard">
            <div class="row">
              <span class="badge badge-warn">{{ DISRUPTION_LABELS[disruption.kind] }}</span>
              <span class="badge" :class="disruption.resolved ? 'badge-ok' : 'badge-danger'">
                {{ disruption.resolved ? 'resolved' : 'unresolved' }}
              </span>
              <span v-if="disruption.date" class="small muted">{{ formatDate(disruption.date) }}</span>
            </div>
            <p>{{ disruption.detail }}</p>
            <p v-if="(disruption.affected_ids ?? []).length > 0" class="small muted">
              Affects {{ labelFor(disruption.affected_ids) }}
            </p>
            <p v-if="disruption.resolution" class="small">Resolution: {{ disruption.resolution }}</p>
          </li>
        </ul>
      </div>

      <!-- Days -->
      <div>
        <h3>Days</h3>
        <p v-if="plan.days.length === 0" class="small muted">
          No days were planned. Check the attractions section above.
        </p>
        <ol class="stack tight">
          <li v-for="(day, index) in plan.days" :key="day.date" class="subcard day">
            <h4>{{ dayHeading(day, index) }}</h4>

            <p v-if="day.forecast" class="forecast small">
              <span>
                {{ day.forecast.summary }},
                {{ Math.round(day.forecast.temp_min_c) }} to {{ Math.round(day.forecast.temp_max_c) }} &deg;C,
                {{ formatPercent(day.forecast.precipitation_chance) }} chance of rain
              </span>
              <span v-if="day.forecast.warning_signal" class="badge badge-warn" role="note">
                Warning: {{ day.forecast.warning_signal }}
              </span>
            </p>
            <p v-else-if="sectionFor('weather')?.status !== 'ok'" class="small muted">
              No forecast available for this day.
            </p>

            <p v-if="(day.items ?? []).length === 0" class="small muted">Nothing scheduled.</p>
            <ul v-else class="items">
              <li v-for="item in day.items" :key="item.item_id" class="item">
                <input
                  type="checkbox"
                  :checked="item.confirmed"
                  :disabled="store.confirming"
                  :aria-label="`Confirm ${item.title}`"
                  @change="toggle($event, (v) => store.setItemConfirmed(item.item_id, v), () => isItemConfirmed(item.item_id))"
                />
                <div class="item-main">
                  <p>
                    <span v-if="formatTimeRange(item.start_time, item.end_time)" class="time">
                      {{ formatTimeRange(item.start_time, item.end_time) }}
                    </span>
                    <strong>{{ item.title }}</strong>
                  </p>
                  <p class="badges">
                    <span v-if="categoryOf(item)" class="badge">{{ categoryOf(item) }}</span>
                    <span v-if="item.needs_reservation" class="badge badge-warn">Reservation needed</span>
                    <span v-if="item.confirmed" class="badge badge-ok">Confirmed</span>
                  </p>
                  <p v-if="item.note" class="small muted">{{ item.note }}</p>
                </div>
              </li>
            </ul>
          </li>
        </ol>
      </div>

      <!-- Hotel -->
      <div>
        <h3>Hotel</h3>
        <div v-if="plan.hotel" class="subcard hotel">
          <input
            type="checkbox"
            :checked="plan.hotel.confirmed"
            :disabled="store.confirming"
            :aria-label="`Confirm hotel ${plan.hotel.hotel.name}`"
            @change="toggle($event, (v) => store.setHotelConfirmed(v), isHotelConfirmed)"
          />
          <div class="item-main">
            <p>
              <strong>{{ plan.hotel.hotel.name }}</strong>
              <span v-if="plan.hotel.confirmed" class="badge badge-ok">Confirmed</span>
            </p>
            <p class="small muted">
              {{ formatDate(plan.hotel.check_in) }} to {{ formatDate(plan.hotel.check_out) }}
              ({{ nightsBetween(plan.hotel.check_in, plan.hotel.check_out) }} nights),
              {{ formatMoneyValue(plan.hotel.hotel.nightly_price) }} per night
            </p>
            <p class="badges">
              <span v-if="plan.hotel.hotel.style" class="badge">{{ plan.hotel.hotel.style }}</span>
              <span v-if="plan.hotel.hotel.rating != null" class="badge">
                Rating {{ plan.hotel.hotel.rating }} / 5
              </span>
              <span v-if="plan.hotel.hotel.distance_km != null" class="badge">
                {{ plan.hotel.hotel.distance_km }} km from the centre
              </span>
            </p>
            <p v-if="plan.hotel.hotel.address" class="small muted">{{ plan.hotel.hotel.address }}</p>
          </div>
        </div>
        <p v-else class="small muted">No hotel in this plan.</p>
      </div>

      <!-- Tickets -->
      <div>
        <h3>Tickets</h3>
        <p v-if="(plan.tickets ?? []).length === 0" class="small muted">
          No tickets in this plan{{ plan.origin ? '' : ' (add an origin to get ticket options)' }}.
        </p>
        <ul v-else class="stack tight">
          <li v-for="ticket in plan.tickets" :key="ticket.ticket_id" class="subcard ticket">
            <input
              type="checkbox"
              :checked="ticket.confirmed"
              :disabled="store.confirming"
              :aria-label="`Confirm ${ticket.direction} ticket ${ticketLabel(ticket)}`"
              @change="toggle($event, (v) => store.setTicketConfirmed(ticket.ticket_id, v), () => isTicketConfirmed(ticket.ticket_id))"
            />
            <div class="item-main">
              <p>
                <span class="badge badge-accent">{{ ticket.direction }}</span>
                <strong>{{ ticket.carrier }}</strong>
                ({{ ticket.mode }})
                <span class="badge" :class="ticketStatusClass(ticket)">{{ ticketStatusText(ticket) }}</span>
                <span v-if="ticket.confirmed" class="badge badge-ok">Confirmed</span>
              </p>
              <p>{{ ticket.origin }} to {{ ticket.destination }}</p>
              <p class="small muted">
                Departs {{ formatDateTime(ticket.depart_at) }}, arrives {{ formatDateTime(ticket.arrive_at) }}
                <span aria-hidden="true">&middot;</span> {{ formatMoneyValue(ticket.price) }}
              </p>
            </div>
          </li>
        </ul>
      </div>

      <!-- Reservations -->
      <div v-if="(plan.reservations ?? []).length > 0">
        <h3>Reservations to make</h3>
        <ul class="stack tight">
          <li v-for="(reservation, index) in plan.reservations" :key="index" class="subcard">
            <p>
              <strong>{{ reservation.place_name }}</strong>
              <span class="small muted"> book at least {{ reservation.lead_time_days }} days ahead</span>
            </p>
            <p v-if="bookingLink(reservation.booking_url)" class="small">
              <a :href="bookingLink(reservation.booking_url)!" target="_blank" rel="noopener noreferrer">
                Booking page
              </a>
            </p>
          </li>
        </ul>
      </div>

      <!-- Saved trips -->
      <p class="small muted">
        <template v-if="(plan.saved_trip_refs ?? []).length > 0">
          Informed by {{ plan.saved_trip_refs!.length }} similar saved
          {{ plan.saved_trip_refs!.length === 1 ? 'trip' : 'trips' }}
          ({{ plan.saved_trip_refs!.join(', ') }}).
        </template>
        <template v-else>No similar saved trips were used for this plan.</template>
      </p>
    </div>
  </section>
</template>

<style scoped>
.destination {
  font-size: 1.4rem;
}

.plan-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

h3 {
  margin-bottom: 8px;
}

h4 {
  margin-bottom: 6px;
}

.stack.tight {
  gap: 8px;
}

.sections {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
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
  font-weight: 500;
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
  border-radius: 999px;
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

.forecast {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin-bottom: 8px;
}

.items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.item,
.hotel,
.ticket {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.item input,
.hotel > input,
.ticket > input {
  flex: none;
  margin-top: 3px;
}

.item-main {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.time {
  margin-right: 6px;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
</style>
