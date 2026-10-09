<script setup lang="ts">
import { computed } from 'vue'
import { CloudSun } from 'lucide-vue-next'
import type { DayPlan, ItineraryItem } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { formatDate, formatPercent } from '@/lib/format'
import { onSelectableKeydown } from './selectable'
import StalenessBadge from './StalenessBadge.vue'
import WorkspaceStop from './WorkspaceStop.vue'

/** One day of the plan: a selectable heading (date and forecast) and its stops in time order. */
const props = defineProps<{ day: DayPlan; index: number; anchorId: string }>()

const planStore = usePlanStore()

const selected = computed(() => planStore.selection?.kind === 'day' && planStore.selection.id === props.day.date)

/** Timed stops first, by start time; untimed ones keep their plan order at the end. */
const stops = computed<ItineraryItem[]>(() =>
  [...(props.day.items ?? [])].sort((a, b) => (a.start_time ?? '99').localeCompare(b.start_time ?? '99')),
)

const toggle = (): void => planStore.select('day', props.day.date)
</script>

<template>
  <li :id="anchorId" class="day part" :class="{ selected }" data-testid="day">
    <h3 class="day-title">
      <span
        role="button"
        tabindex="0"
        class="part-main"
        :aria-pressed="selected"
        @click="toggle"
        @keydown="onSelectableKeydown($event, toggle)"
      >
        Day {{ index + 1 }}<span class="muted date"> · {{ formatDate(day.date) }}</span>
      </span>
    </h3>

    <p class="forecast small">
      <template v-if="day.forecast">
        <CloudSun :size="14" aria-hidden="true" />
        <span>
          {{ day.forecast.summary }}, {{ Math.round(day.forecast.temp_min_c) }} to
          {{ Math.round(day.forecast.temp_max_c) }} &deg;C, {{ formatPercent(day.forecast.precipitation_chance) }} chance of rain
        </span>
        <span v-if="day.forecast.warning_signal" class="badge badge-warn" role="note">
          Warning: {{ day.forecast.warning_signal }}
        </span>
      </template>
      <StalenessBadge agent="weather" :fetched-at="day.forecast?.source.fetched_at" :missing="!day.forecast" />
    </p>

    <ol v-if="stops.length > 0" class="stops" :aria-label="`Day ${index + 1} stops in time order`">
      <WorkspaceStop v-for="item in stops" :key="item.item_id" :item="item" />
    </ol>
    <p v-else class="small muted">Nothing scheduled.</p>
  </li>
</template>

<style scoped>
.day {
  padding: var(--space-3);
  border: 1px solid var(--border);
}

.day-title {
  font-size: var(--text-lg);
}

.date {
  font-size: var(--text-md);
  font-weight: var(--weight-medium);
}

.forecast {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin: var(--space-1) 0 var(--space-2);
  color: var(--text-muted);
}

.stops {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
</style>
