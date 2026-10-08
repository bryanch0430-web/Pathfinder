<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CloudSun } from 'lucide-vue-next'
import type { ItineraryItem } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { formatDate } from '@/lib/format'
import { reorderMessage } from '@/lib/refresh'
import StalenessBadge from './StalenessBadge.vue'
import TimelineItem from './TimelineItem.vue'

/**
 * "Day N" timeline for the selected day. Reordering (drag the handle, or Space + arrow keys on
 * it) sends the new order to the modify path as a chat message: the API has no reorder field.
 * The new order is shown until the server's plan comes back and replaces it.
 */
const planStore = usePlanStore()
const session = useSessionStore()

const day = computed(() => planStore.selectedDay)
const dayNumber = computed(() => planStore.selectedDayIndex + 1)
const serverItems = computed<ItineraryItem[]>(() => day.value?.items ?? [])

/** Optimistic order (item ids) while a reorder is pending, or during a keyboard move. */
const localOrder = ref<string[] | null>(null)
const grabbedId = ref<string | null>(null)
const dragId = ref<string | null>(null)
const overId = ref<string | null>(null)
const announcement = ref('')
let orderBeforeGrab: string[] | null = null

watch(
  () => [planStore.plan, planStore.selectedDayIndex],
  () => {
    localOrder.value = null
    grabbedId.value = null
  },
)

const items = computed<ItineraryItem[]>(() => {
  const order = localOrder.value
  if (!order) return serverItems.value
  const byId = new Map(serverItems.value.map((item) => [item.item_id, item]))
  const ordered = order.map((id) => byId.get(id)).filter((item): item is ItineraryItem => !!item)
  return ordered.length === serverItems.value.length ? ordered : serverItems.value
})

const currentIds = (): string[] => items.value.map((item) => item.item_id)
const serverIds = (): string[] => serverItems.value.map((item) => item.item_id)

function move(ids: string[], id: string, toIndex: number): string[] {
  const next = ids.filter((x) => x !== id)
  next.splice(Math.max(0, Math.min(toIndex, next.length)), 0, id)
  return next
}

function commit(order: string[]): void {
  if (order.join('|') === serverIds().join('|')) {
    localOrder.value = null
    return
  }
  localOrder.value = order
  const byId = new Map(serverItems.value.map((item) => [item.item_id, item.title]))
  const titles = order.map((id) => byId.get(id) ?? id)
  void session.send(reorderMessage(dayNumber.value, titles))
}

// ---- mouse drag and drop -----------------------------------------------------------------------

function onDragstart(event: DragEvent, item: ItineraryItem): void {
  dragId.value = item.item_id
  event.dataTransfer?.setData('text/plain', item.item_id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}

function onDragover(event: DragEvent, item: ItineraryItem): void {
  if (!dragId.value) return
  event.preventDefault()
  overId.value = item.item_id
}

function onDrop(event: DragEvent, target: ItineraryItem): void {
  event.preventDefault()
  const id = dragId.value
  dragId.value = null
  overId.value = null
  if (!id || id === target.item_id) return
  const ids = currentIds()
  commit(move(ids, id, ids.indexOf(target.item_id)))
}

function onDragend(): void {
  dragId.value = null
  overId.value = null
}

// ---- keyboard reorder --------------------------------------------------------------------------

/** Drop a keyboard grab without sending anything (focus left the handle, or another was grabbed). */
function cancelGrab(): void {
  if (grabbedId.value === null) return
  grabbedId.value = null
  localOrder.value = orderBeforeGrab && orderBeforeGrab.join('|') !== serverIds().join('|') ? orderBeforeGrab : null
  orderBeforeGrab = null
  announcement.value = 'Reorder cancelled.'
}

function onHandleBlur(item: ItineraryItem): void {
  // The handle is re-focused after each arrow-key move; only a real focus change cancels.
  requestAnimationFrame(() => {
    const active = document.activeElement
    const stillOnHandle = active instanceof HTMLElement && active.dataset.handleFor === item.item_id
    if (grabbedId.value === item.item_id && !stillOnHandle) cancelGrab()
  })
}

function onHandleKeydown(event: KeyboardEvent, item: ItineraryItem): void {
  const id = item.item_id
  if (event.key === ' ' || event.key === 'Enter') {
    event.preventDefault()
    if (grabbedId.value !== null && grabbedId.value !== id) cancelGrab()
    if (grabbedId.value === id) {
      grabbedId.value = null
      announcement.value = `${item.title} dropped.`
      commit(currentIds())
      orderBeforeGrab = null
    } else {
      grabbedId.value = id
      orderBeforeGrab = currentIds()
      announcement.value = `${item.title} grabbed. Use the up and down arrow keys, then Space to drop or Escape to cancel.`
    }
    return
  }
  if (grabbedId.value !== id) return
  if (event.key === 'Escape') {
    event.preventDefault()
    cancelGrab()
    return
  }
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
  event.preventDefault()
  const ids = currentIds()
  const from = ids.indexOf(id)
  const to = event.key === 'ArrowUp' ? from - 1 : from + 1
  if (to < 0 || to >= ids.length) return
  localOrder.value = move(ids, id, to)
  announcement.value = `${item.title} moved to position ${to + 1} of ${ids.length}.`
  const handle = event.currentTarget as HTMLElement
  requestAnimationFrame(() => handle.focus())
}

function toggleLock(item: ItineraryItem): void {
  void session.setItemConfirmed(item.item_id, !item.confirmed)
}
</script>

<template>
  <section class="card" aria-labelledby="day-title">
    <header class="card-header">
      <div>
        <h2 id="day-title">Day {{ day ? dayNumber : '' }}</h2>
        <p v-if="day" class="small muted">{{ formatDate(day.date) }}</p>
      </div>
      <div v-if="day" class="forecast">
        <span v-if="day.forecast" class="small"><CloudSun :size="14" aria-hidden="true" /> {{ Math.round(day.forecast.temp_max_c) }}°</span>
        <StalenessBadge agent="weather" :fetched-at="day.forecast?.source.fetched_at" :missing="!day.forecast" />
      </div>
    </header>

    <ol v-if="items.length > 0" class="timeline" aria-label="Stops in visiting order" data-testid="timeline">
      <TimelineItem
        v-for="(item, index) in items"
        :key="item.item_id"
        :item="item"
        :place="planStore.placesById.get(item.place_id) ?? null"
        :order="index + 1"
        :selected="item.item_id === planStore.selectedItemId"
        :grabbed="item.item_id === grabbedId"
        :drop-target="item.item_id === overId && overId !== dragId"
        :busy="session.busy"
        @select="planStore.selectItem(item.item_id)"
        @toggle-lock="toggleLock(item)"
        @handle-keydown="onHandleKeydown($event, item)"
        @handle-blur="onHandleBlur(item)"
        @handle-dragstart="onDragstart($event, item)"
        @handle-dragend="onDragend"
        @dragover="onDragover($event, item)"
        @drop="onDrop($event, item)"
      />
    </ol>
    <p v-else class="empty">{{ day ? 'No stops planned for this day.' : 'The day plan appears here once a plan exists.' }}</p>

    <p class="sr-only" aria-live="assertive">{{ announcement }}</p>
  </section>
</template>

<style scoped>
.forecast {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.forecast > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.timeline {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  position: relative;
}

.timeline::before {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: calc(44px + var(--space-2) + 6px);
  width: 2px;
  background: var(--border);
}
</style>
