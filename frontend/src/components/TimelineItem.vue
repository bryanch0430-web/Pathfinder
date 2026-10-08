<script setup lang="ts">
import { computed, ref } from 'vue'
import { GripVertical, Lock, LockOpen } from 'lucide-vue-next'
import type { ItineraryItem, Place } from '@/api'
import { formatMoney, formatTime } from '@/lib/format'

const props = defineProps<{
  item: ItineraryItem
  place: Place | null
  order: number
  selected: boolean
  grabbed: boolean
  dropTarget: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  select: []
  toggleLock: []
  handleKeydown: [event: KeyboardEvent]
  handleBlur: []
  handleDragstart: [event: DragEvent]
  handleDragend: []
}>()

const price = computed(() => {
  const money = props.place?.price
  if (!money) return ''
  return money.amount === 0 ? 'Free' : formatMoney(money.amount, money.currency)
})

const pillText = computed(() => (price.value ? `${props.item.title} · ${price.value}` : props.item.title))
const canDrag = computed(() => !props.item.confirmed && !props.busy)

/**
 * The row is the drag source (browsers do not reliably start a native drag from a <button>),
 * but a drag may only start from the handle: pressing the handle arms the row.
 */
const armed = ref(false)

/** Arm on press; disarm on the next release anywhere (a release outside the handle included). */
function arm(): void {
  armed.value = true
  window.addEventListener('pointerup', () => (armed.value = false), { once: true })
}

function onDragstart(event: DragEvent): void {
  if (!armed.value || !canDrag.value) {
    event.preventDefault()
    return
  }
  emit('handleDragstart', event)
}

function onDragend(): void {
  armed.value = false
  emit('handleDragend')
}
</script>

<template>
  <li
    class="row-item"
    :class="{ selected, grabbed, 'drop-target': dropTarget }"
    :draggable="armed && canDrag"
    @dragstart="onDragstart"
    @dragend="onDragend"
  >
    <span class="time">{{ formatTime(item.start_time) || '—' }}</span>
    <span class="dot" aria-hidden="true" />
    <div class="pill">
      <button
        type="button"
        class="handle"
        :disabled="!canDrag"
        :aria-label="item.confirmed ? `${item.title} is confirmed and keeps its slot` : `Reorder ${item.title}: press Space, then the arrow keys`"
        :aria-pressed="grabbed"
        :title="item.confirmed ? 'Confirmed items keep their slot' : 'Drag to reorder'"
        :data-handle-for="item.item_id"
        @keydown="emit('handleKeydown', $event)"
        @blur="emit('handleBlur')"
        @pointerdown="arm"
      >
        <GripVertical :size="14" aria-hidden="true" />
      </button>
      <button type="button" class="name" :aria-current="selected ? 'true' : undefined" @click="emit('select')">
        <span class="sr-only">Stop {{ order }}: </span>{{ pillText }}
      </button>
      <button
        type="button"
        class="lock"
        :class="{ on: item.confirmed }"
        :aria-pressed="item.confirmed"
        :aria-label="item.confirmed ? `Unconfirm ${item.title}` : `Confirm ${item.title} (keep it across edits)`"
        :title="item.confirmed ? 'Confirmed: kept across edits' : 'Confirm to keep across edits'"
        :disabled="busy"
        @click="emit('toggleLock')"
      >
        <Lock v-if="item.confirmed" :size="13" aria-hidden="true" />
        <LockOpen v-else :size="13" aria-hidden="true" />
      </button>
    </div>
  </li>
</template>

<style scoped>
.row-item {
  display: grid;
  grid-template-columns: 44px 14px minmax(0, 1fr);
  align-items: center;
  gap: var(--space-2);
  position: relative;
}

.time {
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--border-strong);
  background: var(--card);
  justify-self: center;
}

.selected .dot {
  border-color: var(--accent);
  background: var(--accent);
}

.pill {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--card);
  box-shadow: var(--shadow-card);
}

.selected .pill {
  border-color: var(--accent);
}

.grabbed .pill {
  border-color: var(--primary);
  box-shadow: var(--shadow-float);
}

.drop-target .pill {
  border-style: dashed;
  border-color: var(--primary);
}

.handle,
.lock {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--icon-muted);
}

.handle {
  cursor: grab;
}

.handle:disabled {
  cursor: default;
  opacity: 0.4;
}

.lock {
  cursor: pointer;
}

.lock.on {
  color: var(--accent-strong);
}

.lock:disabled {
  cursor: not-allowed;
}

.name {
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  text-align: left;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow-wrap: anywhere;
  cursor: pointer;
}
</style>
