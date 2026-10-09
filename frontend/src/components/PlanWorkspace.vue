<script setup lang="ts">
import { computed } from 'vue'
import { Star } from 'lucide-vue-next'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import { formatDate, formatShortDate } from '@/lib/format'
import WorkspaceBookings from './WorkspaceBookings.vue'
import WorkspaceDay from './WorkspaceDay.vue'
import WorkspaceDetails from './WorkspaceDetails.vue'

/**
 * The plan workspace (center column): the whole itinerary with selectable days, stops, hotel and
 * tickets. The selection scopes the next chat message; Escape in here (outside the stop editor) or
 * in the chat composer clears it.
 */
const planStore = usePlanStore()
const session = useSessionStore()
const ui = useUiStore()

const plan = computed(() => planStore.plan)

const subtitle = computed(() => {
  const p = plan.value
  if (!p) return ''
  const parts = [`${formatDate(p.start_date)} – ${formatDate(p.end_date)}`]
  parts.push(`${p.party_size} ${p.party_size === 1 ? 'traveller' : 'travellers'}`)
  if (p.origin) parts.push(`from ${p.origin}`)
  return parts.join(' · ')
})

const rated = computed(() => {
  const outcome = session.feedbackOutcome
  const p = plan.value
  return outcome && p && outcome.planId === p.plan_id && outcome.version === p.version ? outcome : null
})

const dayAnchor = (date: string): string => `ws-day-${date}`

/** The day-jump strip scrolls to a day and points the map at it; it does not scope the chat. */
function jumpTo(index: number, date: string): void {
  planStore.selectDay(index)
  document.getElementById(dayAnchor(date))?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && planStore.selection) planStore.clearSelection()
}
</script>

<template>
  <section class="card workspace" aria-labelledby="workspace-title" @keydown="onKeydown">
    <template v-if="!plan">
      <h2 id="workspace-title" class="empty-title">Your plan</h2>
      <p class="empty">Fill in your trip on the left and press Generate.</p>
    </template>

    <template v-else>
      <header class="ws-head">
        <div class="title-block">
          <h2 id="workspace-title">{{ plan.destination }}</h2>
          <p class="small muted">{{ subtitle }}</p>
        </div>
        <div class="row head-actions">
          <span class="badge badge-accent" :title="`Plan ${plan.plan_id}`">Version {{ plan.version }}</span>
          <button v-if="!rated" type="button" class="btn btn-small btn-primary" @click="ui.openDialog('rating')">
            Mark as useful
          </button>
          <p v-else class="saved small" role="status">
            <Star :size="14" aria-hidden="true" />
            Saved as useful · {{ rated.rating }}/5
          </p>
        </div>
      </header>

      <nav v-if="plan.days.length > 1" class="jump" aria-label="Jump to a day">
        <button
          v-for="(day, index) in plan.days"
          :key="day.date"
          type="button"
          class="chip"
          :class="{ current: index === planStore.selectedDayIndex }"
          :aria-current="index === planStore.selectedDayIndex ? 'true' : undefined"
          :aria-label="`Day ${index + 1}, ${formatDate(day.date)}`"
          @click="jumpTo(index, day.date)"
        >
          Day {{ index + 1 }}<span class="muted"> · {{ formatShortDate(day.date) }}</span>
        </button>
      </nav>

      <p class="small muted hint">
        Select a day, stop, hotel or ticket to ask about just that part; select it again (or press Escape) to clear.
      </p>

      <p v-if="plan.days.length === 0" class="small muted">No days were planned. Check the data sections below.</p>
      <ol v-else class="days" aria-label="Days">
        <WorkspaceDay
          v-for="(day, index) in plan.days"
          :key="day.date"
          :day="day"
          :index="index"
          :anchor-id="dayAnchor(day.date)"
        />
      </ol>

      <WorkspaceBookings />
      <WorkspaceDetails />
    </template>
  </section>
</template>

<style scoped>
.workspace {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.empty-title {
  margin-bottom: calc(-1 * var(--space-2));
}

.ws-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.title-block {
  min-width: 0;
}

.title-block h2 {
  font-size: var(--text-xl);
  overflow-wrap: anywhere;
}

.saved {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ok);
}

.jump {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.jump .current {
  border-color: var(--primary);
  background: var(--primary);
  color: var(--primary-text);
}

.jump .current .muted {
  color: inherit;
}

.hint {
  margin-top: calc(-1 * var(--space-2));
}

.days {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>
