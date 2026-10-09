<script setup lang="ts">
import { computed, reactive, ref, useId, watch } from 'vue'
import type { ConstraintKind, HardConstraint, TripContext } from '@/api'
import { useSessionStore } from '@/stores/session'
import {
  CONSTRAINT_KINDS,
  CONSTRAINT_LABELS,
  DEFAULT_CURRENCY,
  HOTEL_STYLES,
} from '@/lib/constants'
import { KEY_VARIABLE_LABELS, missingKeyVariables } from '@/lib/context'

const store = useSessionStore()
const uid = useId()
const id = (name: string): string => `${uid}-${name}`

type NumberInput = number | string | null

interface Draft {
  destination: string
  origin: string
  startDate: string
  endMode: 'date' | 'days'
  endDate: string
  days: NumberInput
  partySize: NumberInput
  budgetAmount: NumberInput
  currency: string
  hotelStyle: string
  constraints: HardConstraint[]
}

function blankDraft(): Draft {
  return {
    destination: '',
    origin: '',
    startDate: '',
    endMode: 'date',
    endDate: '',
    days: null,
    partySize: null,
    budgetAmount: null,
    currency: DEFAULT_CURRENCY,
    hotelStyle: 'any',
    constraints: [],
  }
}

function fromContext(context: TripContext): Draft {
  return {
    destination: context.destination ?? '',
    origin: context.origin ?? '',
    startDate: context.start_date ?? '',
    endMode: context.days != null && !context.end_date ? 'days' : 'date',
    endDate: context.end_date ?? '',
    days: context.days ?? null,
    partySize: context.party_size ?? null,
    budgetAmount: context.budget?.amount ?? null,
    currency: context.budget?.currency ?? DEFAULT_CURRENCY,
    hotelStyle: context.hotel_style ?? 'any',
    constraints: (context.hard_constraints ?? []).map((c) => ({ kind: c.kind, value: c.value })),
  }
}

function toNumber(value: NumberInput): number | null {
  if (value === null || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const trimOrNull = (value: string): string | null => value.trim() || null

function toContext(draft: Draft): TripContext {
  const amount = toNumber(draft.budgetAmount)
  return {
    destination: trimOrNull(draft.destination),
    origin: trimOrNull(draft.origin),
    start_date: draft.startDate || null,
    end_date: draft.endMode === 'date' ? draft.endDate || null : null,
    days: draft.endMode === 'days' ? toNumber(draft.days) : null,
    party_size: toNumber(draft.partySize),
    budget: amount === null ? null : { amount, currency: draft.currency.trim().toUpperCase() },
    hotel_style: draft.hotelStyle === 'any' ? null : draft.hotelStyle,
    hard_constraints: draft.constraints.map((c) => ({ kind: c.kind, value: c.value.trim() })),
  }
}

const keyOf = (draft: Draft): string => JSON.stringify(toContext(draft))

// ---- draft state -------------------------------------------------------------------------------

const draft = reactive<Draft>(blankDraft())
/** Serialised form of the server-side context the draft was last aligned with. */
const syncedKey = ref(keyOf(draft))

watch(
  () => store.context,
  (context) => {
    const incoming = fromContext(context)
    // Adopt the server copy only if the user has no unsaved edits in the form.
    if (keyOf(draft) === syncedKey.value) Object.assign(draft, incoming)
    syncedKey.value = keyOf(incoming)
  },
  { deep: true, immediate: true },
)

const dirty = computed(() => keyOf(draft) !== syncedKey.value)
const draftContext = computed(() => toContext(draft))
const missing = computed(() => missingKeyVariables(draftContext.value))

const hotelStyleOptions = computed(() => {
  const options: string[] = [...HOTEL_STYLES]
  if (!options.includes(draft.hotelStyle)) options.push(draft.hotelStyle)
  return options
})

// ---- validation --------------------------------------------------------------------------------

const errors = computed(() => {
  const found: Record<string, string> = {}
  const ctx = draftContext.value
  if ((ctx.destination ?? '').length > 200) found.destination = 'Use at most 200 characters.'
  if ((ctx.origin ?? '').length > 200) found.origin = 'Use at most 200 characters.'
  if (ctx.start_date && ctx.end_date && ctx.end_date < ctx.start_date) {
    found.endDate = 'The end date must not be before the start date.'
  }
  const days = toNumber(draft.days)
  if (draft.endMode === 'days' && days !== null && (!Number.isInteger(days) || days < 1 || days > 60)) {
    found.days = 'Enter a whole number of days from 1 to 60.'
  }
  const party = toNumber(draft.partySize)
  if (party !== null && (!Number.isInteger(party) || party < 1 || party > 50)) {
    found.partySize = 'Enter a whole number from 1 to 50.'
  }
  const amount = toNumber(draft.budgetAmount)
  if (amount !== null && amount < 0) found.budgetAmount = 'The budget cannot be negative.'
  if (amount !== null && !/^[A-Za-z]{3}$/.test(draft.currency.trim())) {
    found.currency = 'Use a 3-letter ISO currency code, e.g. JPY.'
  }
  return found
})

const hasErrors = computed(() => Object.keys(errors.value).length > 0)

// ---- constraints -------------------------------------------------------------------------------

const newKind = ref<ConstraintKind>('must_visit')
const newValue = ref('')
const constraintError = ref('')

const kindHint = computed(() => CONSTRAINT_KINDS.find((k) => k.value === newKind.value)?.hint ?? '')

function addConstraint(): void {
  const value = newValue.value.trim()
  if (!value) {
    constraintError.value = 'Enter a value for the constraint.'
    return
  }
  if (value.length > 500) {
    constraintError.value = 'Use at most 500 characters.'
    return
  }
  constraintError.value = ''
  draft.constraints.push({ kind: newKind.value, value })
  newValue.value = ''
}

function removeConstraint(index: number): void {
  draft.constraints.splice(index, 1)
}

// ---- actions -----------------------------------------------------------------------------------

/** Save the draft as the trip context; false when it has errors or the request failed. */
async function save(): Promise<boolean> {
  if (hasErrors.value) return false
  const saved = await store.saveContext(draftContext.value)
  if (saved) {
    Object.assign(draft, fromContext(store.context))
    syncedKey.value = keyOf(draft)
  }
  return saved
}

function revert(): void {
  Object.assign(draft, fromContext(store.context))
  syncedKey.value = keyOf(draft)
}
</script>

<template>
  <section class="trip-form" :aria-labelledby="id('title')">
    <header class="form-head">
      <h2 :id="id('title')" class="sr-only">Trip details form</h2>
      <span class="small muted" role="status">
        {{ store.savingContext ? 'Saving...' : dirty ? 'Unsaved changes' : 'Saved' }}
      </span>
    </header>

    <form class="stack" novalidate @submit.prevent="save">
      <div
        class="missing"
        :class="missing.length === 0 ? 'missing-ok' : 'missing-warn'"
        role="status"
        aria-live="polite"
      >
        <template v-if="missing.length === 0">
          All key details are filled in. Pathfinder can plan without asking follow-up questions.
        </template>
        <template v-else>
          <strong>Still missing:</strong>
          <ul class="missing-list">
            <li v-for="key in missing" :key="key">{{ KEY_VARIABLE_LABELS[key] }}</li>
          </ul>
          <span class="small">
            Pathfinder asks for these instead of guessing{{ dirty ? ' (based on your unsaved edits)' : '' }}.
          </span>
        </template>
      </div>

      <div class="grid">
        <div class="span-2">
          <label :for="id('destination')">Destination</label>
          <input
            :id="id('destination')"
            v-model="draft.destination"
            type="text"
            maxlength="200"
            autocomplete="off"
            placeholder="e.g. Kyoto"
            :aria-invalid="!!errors.destination"
          />
          <p v-if="errors.destination" class="field-error">{{ errors.destination }}</p>
        </div>

        <div class="span-2">
          <label :for="id('origin')">Origin (optional)</label>
          <input
            :id="id('origin')"
            v-model="draft.origin"
            type="text"
            maxlength="200"
            autocomplete="off"
            placeholder="Departure city, used for tickets"
          />
        </div>

        <div>
          <label :for="id('start')">Start date</label>
          <input :id="id('start')" v-model="draft.startDate" type="date" />
        </div>

        <fieldset>
          <legend>Trip length</legend>
          <div class="row mode">
            <label class="inline">
              <input v-model="draft.endMode" type="radio" value="date" :name="id('mode')" />
              End date
            </label>
            <label class="inline">
              <input v-model="draft.endMode" type="radio" value="days" :name="id('mode')" />
              Days
            </label>
          </div>
          <input
            v-if="draft.endMode === 'date'"
            v-model="draft.endDate"
            type="date"
            :min="draft.startDate || undefined"
            aria-label="End date"
            :aria-invalid="!!errors.endDate"
          />
          <input
            v-else
            v-model.number="draft.days"
            type="number"
            min="1"
            max="60"
            step="1"
            inputmode="numeric"
            placeholder="Number of days"
            aria-label="Number of days"
            :aria-invalid="!!errors.days"
          />
          <p v-if="errors.endDate" class="field-error">{{ errors.endDate }}</p>
          <p v-if="errors.days" class="field-error">{{ errors.days }}</p>
        </fieldset>

        <div>
          <label :for="id('party')">Party size</label>
          <input
            :id="id('party')"
            v-model.number="draft.partySize"
            type="number"
            min="1"
            max="50"
            step="1"
            inputmode="numeric"
            placeholder="Travellers"
            :aria-invalid="!!errors.partySize"
          />
          <p v-if="errors.partySize" class="field-error">{{ errors.partySize }}</p>
        </div>

        <div>
          <label :for="id('hotel-style')">Hotel style</label>
          <select :id="id('hotel-style')" v-model="draft.hotelStyle">
            <option v-for="style in hotelStyleOptions" :key="style" :value="style">{{ style }}</option>
          </select>
        </div>

        <fieldset class="span-2">
          <legend>Total budget</legend>
          <div class="budget">
            <input
              v-model.number="draft.budgetAmount"
              type="number"
              min="0"
              step="any"
              inputmode="decimal"
              placeholder="Amount"
              aria-label="Budget amount"
              :aria-invalid="!!errors.budgetAmount"
            />
            <input
              v-model="draft.currency"
              type="text"
              maxlength="3"
              autocapitalize="characters"
              autocomplete="off"
              placeholder="JPY"
              aria-label="Budget currency (3-letter ISO code)"
              :aria-invalid="!!errors.currency"
            />
          </div>
          <p v-if="errors.budgetAmount" class="field-error">{{ errors.budgetAmount }}</p>
          <p v-if="errors.currency" class="field-error">{{ errors.currency }}</p>
        </fieldset>
      </div>

      <fieldset class="constraints">
        <legend>Hard constraints</legend>
        <p class="field-hint">Rules the plan must respect. Violations are flagged on the itinerary.</p>

        <ul v-if="draft.constraints.length > 0" class="constraint-list" aria-label="Hard constraints">
          <li v-for="(constraint, index) in draft.constraints" :key="`${constraint.kind}-${index}-${constraint.value}`">
            <span class="badge badge-accent">{{ CONSTRAINT_LABELS[constraint.kind] }}</span>
            <span class="constraint-value">{{ constraint.value }}</span>
            <button
              type="button"
              class="btn btn-small"
              :aria-label="`Remove ${CONSTRAINT_LABELS[constraint.kind]} constraint: ${constraint.value}`"
              @click="removeConstraint(index)"
            >
              Remove
            </button>
          </li>
        </ul>
        <p v-else class="small muted">No constraints yet.</p>

        <div class="constraint-add">
          <div>
            <label :for="id('kind')">Kind</label>
            <select :id="id('kind')" v-model="newKind">
              <option v-for="kind in CONSTRAINT_KINDS" :key="kind.value" :value="kind.value">
                {{ kind.label }}
              </option>
            </select>
          </div>
          <div>
            <label :for="id('value')">Value</label>
            <input
              :id="id('value')"
              v-model="newValue"
              type="text"
              maxlength="500"
              autocomplete="off"
              :placeholder="kindHint"
              @keydown.enter.prevent="addConstraint"
            />
          </div>
          <button type="button" class="btn" @click="addConstraint">Add</button>
        </div>
        <p v-if="constraintError" class="field-error" role="alert">{{ constraintError }}</p>
      </fieldset>

      <div class="row actions">
        <!-- An embedding panel can replace the buttons, e.g. with Generate (save, then plan). -->
        <slot name="actions" :save="save" :revert="revert" :has-errors="hasErrors" :dirty="dirty">
          <button type="submit" class="btn btn-primary" :disabled="store.savingContext || hasErrors || !dirty">
            {{ store.savingContext ? 'Saving...' : 'Save trip details' }}
          </button>
          <button type="button" class="btn" :disabled="!dirty || store.savingContext" @click="revert">
            Discard edits
          </button>
        </slot>
      </div>
    </form>
  </section>
</template>

<style scoped>
/* Sized by its container: the form also sits in the narrow left column. */
.trip-form {
  container-type: inline-size;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.span-2 {
  grid-column: 1 / -1;
}

.missing {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  font-size: 0.9rem;
}

.missing-ok {
  background: var(--ok-soft);
  color: var(--ok);
}

.missing-warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.missing-list {
  margin: 4px 0;
  padding-left: 18px;
  list-style: disc;
}

label.inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-weight: 500;
}

.mode {
  margin-bottom: 6px;
}

.budget {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px;
  gap: 8px;
}

.budget input:last-child {
  text-transform: uppercase;
}

.constraint-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 8px 0;
}

.constraint-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: var(--card-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.constraint-value {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.constraint-add {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr) auto;
  align-items: end;
  gap: 8px;
  margin-top: 8px;
}

.actions {
  justify-content: flex-start;
}

@container (max-width: 400px) {
  .constraint-add {
    grid-template-columns: minmax(0, 1fr);
  }
}

@container (max-width: 340px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

<style scoped>
.form-head {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-2);
}
</style>
