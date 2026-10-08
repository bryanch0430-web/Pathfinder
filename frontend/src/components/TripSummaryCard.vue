<script setup lang="ts">
import { computed } from 'vue'
import { Check, CircleAlert, Star } from 'lucide-vue-next'
import type { AgentName } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import { formatShortDate } from '@/lib/format'

const planStore = usePlanStore()
const session = useSessionStore()
const ui = useUiStore()

const plan = computed(() => planStore.plan)

const title = computed(() => {
  const p = plan.value
  if (!p) return ''
  const n = p.days.length
  return `${n} ${n === 1 ? 'Day' : 'Days'} in ${p.destination}`
})

const subtitle = computed(() => {
  const p = plan.value
  if (!p) return ''
  const parts = [`${formatShortDate(p.start_date)} – ${formatShortDate(p.end_date)}`]
  parts.push(`${p.party_size} ${p.party_size === 1 ? 'traveller' : 'travellers'}`)
  if (p.origin) parts.push(`from ${p.origin}`)
  return parts.join(' · ')
})

interface Covered {
  agent: AgentName
  label: string
  detail: string
}

const covered = computed<Covered[]>(() => {
  const p = plan.value
  if (!p) return []
  const stops = p.days.reduce((sum, day) => sum + (day.items?.length ?? 0), 0)
  const forecasts = p.days.filter((day) => day.forecast).length
  const tickets = p.tickets?.length ?? 0
  return [
    { agent: 'hotel', label: 'Accommodation', detail: p.hotel?.hotel.name ?? 'not found yet' },
    { agent: 'attraction', label: 'Attractions', detail: `${stops} ${stops === 1 ? 'stop' : 'stops'}` },
    { agent: 'ticket', label: 'Tickets', detail: tickets > 0 ? `${tickets} ${tickets === 1 ? 'ticket' : 'tickets'}` : 'none needed' },
    { agent: 'weather', label: 'Weather', detail: `${forecasts}-day forecast` },
  ]
})

const statusOf = (agent: AgentName) => planStore.sectionsByAgent.get(agent)?.status ?? 'ok'

const rated = computed(() => {
  const outcome = session.feedbackOutcome
  const p = plan.value
  return outcome && p && outcome.planId === p.plan_id && outcome.version === p.version ? outcome : null
})
</script>

<template>
  <section v-if="plan" class="card" aria-labelledby="summary-title">
    <h2 id="summary-title">{{ title }}</h2>
    <p class="small muted subtitle">{{ subtitle }}</p>

    <ul class="covered" aria-label="What the plan covers">
      <li v-for="item in covered" :key="item.agent">
        <span class="icon" :class="statusOf(item.agent)" aria-hidden="true">
          <Check v-if="statusOf(item.agent) === 'ok'" :size="12" />
          <CircleAlert v-else :size="12" />
        </span>
        <span>{{ item.label }}</span>
        <span class="muted small detail">
          {{ item.detail }}<template v-if="statusOf(item.agent) !== 'ok'"> ({{ statusOf(item.agent) }})</template>
        </span>
      </li>
    </ul>

    <div class="actions">
      <button v-if="!rated" type="button" class="btn btn-primary" @click="ui.openDialog('rating')">
        Mark as useful
      </button>
      <p v-else class="saved small" role="status">
        <Star :size="14" aria-hidden="true" />
        Saved as useful · {{ rated.rating }}/5
      </p>
      <button type="button" class="btn" @click="ui.openDialog('trip-details')">Trip details</button>
    </div>
  </section>
</template>

<style scoped>
.subtitle {
  margin-top: 2px;
}

.covered {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-4) 0;
  font-size: var(--text-sm);
}

.covered li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.detail {
  margin-left: auto;
  text-align: right;
}

.icon {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--ok-soft);
  color: var(--ok);
}

.icon.stale,
.icon.unavailable {
  background: var(--unavailable-soft);
  color: var(--unavailable);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.saved {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 40px;
  padding: 0 14px;
  border-radius: var(--radius-pill);
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-weight: var(--weight-medium);
}
</style>
