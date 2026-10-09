<script setup lang="ts">
import { computed } from 'vue'
import { Lock } from 'lucide-vue-next'
import type { ItineraryItem } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { formatMoney, formatTimeRange } from '@/lib/format'
import { onSelectableKeydown } from './selectable'
import StopEditor from './StopEditor.vue'

/** One stop in a day section. Click (or Enter/Space) selects it; the selected stop shows its editor. */
const props = defineProps<{ item: ItineraryItem }>()

const planStore = usePlanStore()

const place = computed(() => planStore.placesById.get(props.item.place_id) ?? null)
const selected = computed(
  () => planStore.selection?.kind === 'item' && planStore.selection.id === props.item.item_id,
)
const time = computed(() => formatTimeRange(props.item.start_time, props.item.end_time))
const price = computed(() => {
  const money = place.value?.price
  if (!money) return ''
  return money.amount === 0 ? 'Free' : formatMoney(money.amount, money.currency)
})

const toggle = (): void => planStore.select('item', props.item.item_id)
</script>

<template>
  <li class="stop part" :class="{ selected }" data-testid="stop">
    <div
      role="button"
      tabindex="0"
      class="part-main stop-main"
      :aria-pressed="selected"
      @click="toggle"
      @keydown="onSelectableKeydown($event, toggle)"
    >
      <span class="time">{{ time || 'Any time' }}</span>
      <span class="body">
        <span class="title">{{ item.title }}</span><span v-if="price" class="muted"> · {{ price }}</span>
        <span class="badges">
          <span v-if="place?.category" class="badge">{{ place.category }}</span>
          <span v-if="item.needs_reservation" class="badge badge-warn">Reservation needed</span>
          <span v-if="item.confirmed" class="badge badge-ok"><Lock :size="11" aria-hidden="true" /> Locked</span>
        </span>
        <span v-if="item.note" class="note small muted">{{ item.note }}</span>
      </span>
    </div>
    <StopEditor v-if="selected" :item="item" />
  </li>
</template>

<style scoped>
.stop {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border);
  background: var(--card-soft);
}

.stop-main {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: var(--space-2);
  align-items: start;
}

.time {
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
  padding-top: 1px;
}

.body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.title {
  font-weight: var(--weight-medium);
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

@media (max-width: 420px) {
  .stop-main {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
}
</style>
