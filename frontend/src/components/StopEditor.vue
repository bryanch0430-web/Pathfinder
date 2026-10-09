<script setup lang="ts">
import { computed, reactive, ref, useId, watch } from 'vue'
import { Lock, LockOpen, Trash2 } from 'lucide-vue-next'
import type { ItineraryItem, PlanItemPatch } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore, type PlanEditResult } from '@/stores/session'
import { formatDate, formatTime } from '@/lib/format'
import { lockError } from './selectable'

/**
 * Manual edits for the selected stop: times, note, move to another day, delete and lock. Every
 * change goes to the server and the returned plan replaces the local one (never optimistic);
 * a refused change shows the server message next to its control.
 */
const props = defineProps<{ item: ItineraryItem }>()

const planStore = usePlanStore()
const session = useSessionStore()
const uid = useId()
const id = (name: string): string => `${uid}-${name}`

type Field = 'times' | 'note' | 'day' | 'remove' | 'lock'

const start = ref('')
const end = ref('')
const note = ref('')
const day = ref('')
const errors = reactive<Partial<Record<Field, string>>>({})

const currentDay = computed(
  () => planStore.days.find((d) => d.items?.some((i) => i.item_id === props.item.item_id))?.date ?? '',
)

watch(
  () => [props.item, currentDay.value] as const,
  ([item, date]) => {
    start.value = formatTime(item.start_time)
    end.value = formatTime(item.end_time)
    note.value = item.note ?? ''
    day.value = date
  },
  { immediate: true },
)

const locked = computed(() => props.item.confirmed)
const pending = computed(() => session.editingItemId !== null || session.busy || session.confirming)
const disabled = computed(() => pending.value || locked.value)

const timesChanged = computed(
  () => start.value !== formatTime(props.item.start_time) || end.value !== formatTime(props.item.end_time),
)
const noteChanged = computed(() => (note.value.trim() || null) !== (props.item.note ?? null))

async function run(field: Field, call: () => Promise<PlanEditResult>): Promise<void> {
  delete errors[field]
  const result = await call()
  if (!result.ok) errors[field] = result.error ?? 'The change was not saved.'
}

function saveTimes(): void {
  const patch: PlanItemPatch = {}
  if (start.value !== formatTime(props.item.start_time)) patch.start_time = start.value
  if (end.value !== formatTime(props.item.end_time)) patch.end_time = end.value
  if (patch.start_time === '' || patch.end_time === '') {
    errors.times = 'Enter both times; a time cannot be cleared.'
    return
  }
  if (Object.keys(patch).length === 0) return
  void run('times', () => session.editItem(props.item.item_id, patch))
}

function saveNote(): void {
  if (!noteChanged.value) return
  void run('note', () => session.editItem(props.item.item_id, { note: note.value.trim() || null }))
}

function moveDay(): void {
  if (!day.value || day.value === currentDay.value) return
  void run('day', () => session.editItem(props.item.item_id, { day: day.value }))
}

function remove(): void {
  void run('remove', () => session.deleteItem(props.item.item_id))
}

async function toggleLock(): Promise<void> {
  delete errors.lock
  const message = await lockError(() => session.setItemConfirmed(props.item.item_id, !locked.value))
  if (message) errors.lock = message
}
</script>

<template>
  <!-- Escape stays in here: the workspace would clear the selection, unmounting unsaved edits. -->
  <div
    class="editor"
    :aria-label="`Edit ${item.title}`"
    role="group"
    data-testid="stop-editor"
    @keydown.escape.stop
  >
    <p v-if="locked" class="small muted">Locked: unlock it to change its time, note or day.</p>

    <form class="times" novalidate @submit.prevent="saveTimes">
      <div>
        <label :for="id('start')">Start</label>
        <input :id="id('start')" v-model="start" type="time" :disabled="disabled" />
      </div>
      <div>
        <label :for="id('end')">End</label>
        <input :id="id('end')" v-model="end" type="time" :disabled="disabled" />
      </div>
      <button type="submit" class="btn btn-small" :disabled="disabled || !timesChanged">Save times</button>
      <p v-if="errors.times" class="field-error span-all" role="alert">{{ errors.times }}</p>
    </form>

    <form class="stack-sm" novalidate @submit.prevent="saveNote">
      <label :for="id('note')">Note</label>
      <textarea :id="id('note')" v-model="note" rows="2" maxlength="500" :disabled="disabled" />
      <div class="row">
        <button type="submit" class="btn btn-small" :disabled="disabled || !noteChanged">Save note</button>
      </div>
      <p v-if="errors.note" class="field-error" role="alert">{{ errors.note }}</p>
    </form>

    <form class="move" novalidate @submit.prevent="moveDay">
      <div>
        <label :for="id('day')">Day</label>
        <select :id="id('day')" v-model="day" :disabled="disabled">
          <option v-for="(d, index) in planStore.days" :key="d.date" :value="d.date">
            Day {{ index + 1 }} · {{ formatDate(d.date) }}
          </option>
        </select>
      </div>
      <button type="submit" class="btn btn-small" :disabled="disabled || day === currentDay">Move</button>
      <p v-if="errors.day" class="field-error span-all" role="alert">{{ errors.day }}</p>
    </form>

    <div class="row actions">
      <div>
        <button
          type="button"
          class="btn btn-small"
          :aria-pressed="locked"
          :aria-label="locked ? `Unlock ${item.title}` : `Lock ${item.title} (keep it across edits)`"
          :disabled="pending"
          @click="toggleLock"
        >
          <Lock v-if="locked" :size="14" aria-hidden="true" />
          <LockOpen v-else :size="14" aria-hidden="true" />
          {{ locked ? 'Unlock' : 'Lock' }}
        </button>
        <p v-if="errors.lock" class="field-error" role="alert">{{ errors.lock }}</p>
      </div>
      <div>
        <button type="button" class="btn btn-small danger" :disabled="disabled" @click="remove">
          <Trash2 :size="14" aria-hidden="true" />
          Delete stop
        </button>
        <p v-if="errors.remove" class="field-error" role="alert">{{ errors.remove }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-2);
  padding: var(--space-3);
  border-top: 1px solid var(--border);
}

.times,
.move {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  align-items: end;
  gap: var(--space-2);
}

.move {
  grid-template-columns: minmax(0, 1fr) auto;
}

.span-all {
  grid-column: 1 / -1;
}

.stack-sm {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.actions {
  align-items: flex-start;
  justify-content: space-between;
}

.danger {
  color: var(--danger);
}
</style>
