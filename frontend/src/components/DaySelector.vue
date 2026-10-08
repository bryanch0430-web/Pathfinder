<script setup lang="ts">
import { computed } from 'vue'
import { usePlanStore } from '@/stores/plan'
import { formatDate, formatShortDate } from '@/lib/format'

/** "Travel Plan": numbered day circles; the active one is ringed in the accent colour. */
const planStore = usePlanStore()

const range = computed(() => {
  const p = planStore.plan
  return p ? `${formatShortDate(p.start_date)} – ${formatShortDate(p.end_date)}` : ''
})

function onKeydown(event: KeyboardEvent, index: number): void {
  const count = planStore.days.length
  let next = -1
  if (event.key === 'ArrowRight') next = Math.min(count - 1, index + 1)
  else if (event.key === 'ArrowLeft') next = Math.max(0, index - 1)
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = count - 1
  if (next === -1 || next === index) return
  event.preventDefault()
  planStore.selectDay(next)
  const buttons = (event.currentTarget as HTMLElement).parentElement?.parentElement?.querySelectorAll('button')
  buttons?.[next]?.focus()
}
</script>

<template>
  <section class="card" aria-labelledby="travel-plan-title">
    <header class="card-header">
      <h2 id="travel-plan-title">Travel Plan</h2>
      <span class="small muted">{{ range }}</span>
    </header>
    <ol v-if="planStore.days.length > 0" class="days" aria-label="Days">
      <li v-for="(day, index) in planStore.days" :key="day.date">
        <button
          type="button"
          class="day"
          :class="{ active: index === planStore.selectedDayIndex }"
          :aria-pressed="index === planStore.selectedDayIndex"
          :aria-label="`Day ${index + 1}, ${formatDate(day.date)}`"
          :tabindex="index === planStore.selectedDayIndex ? 0 : -1"
          @click="planStore.selectDay(index)"
          @keydown="onKeydown($event, index)"
        >
          {{ index + 1 }}
        </button>
        <span class="date tiny" aria-hidden="true">{{ formatShortDate(day.date) }}</span>
      </li>
    </ol>
    <p v-else class="empty">Days appear here once a plan exists.</p>
  </section>
</template>

<style scoped>
.days {
  display: flex;
  gap: var(--space-3);
  overflow-x: auto;
  padding: 6px 4px 2px;
}

.days li {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.day {
  display: inline-grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  background: var(--card);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.day:hover:not(.active) {
  border-color: var(--icon-muted);
}

.day.active {
  border-color: var(--primary);
  background: var(--primary);
  color: var(--primary-text);
  box-shadow: 0 0 0 3px var(--card), 0 0 0 6px var(--accent);
}

.day.active:focus-visible {
  box-shadow: 0 0 0 3px var(--card), 0 0 0 6px var(--focus);
}

.date {
  color: var(--text-muted);
  white-space: nowrap;
}
</style>
