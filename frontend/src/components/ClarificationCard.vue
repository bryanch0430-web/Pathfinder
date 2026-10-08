<script setup lang="ts">
import { computed, reactive, ref, useId } from 'vue'
import { MessageCircleQuestion } from 'lucide-vue-next'
import type { TripContext } from '@/api'
import type { ChatEntry } from '@/stores/session'
import { useSessionStore } from '@/stores/session'
import { DEFAULT_CURRENCY } from '@/lib/constants'

/**
 * The router asked for clarification. Missing key variables (destination, dates, party size,
 * budget) get inline inputs; "Continue" saves them to the trip context and resends the message
 * that triggered the question. An "unclear" route with no missing field gets a free-text answer.
 * Only the latest clarification is interactive; older ones render read-only.
 */
const props = defineProps<{ entry: ChatEntry; interactive: boolean }>()

const session = useSessionStore()
const uid = useId()
const id = (name: string): string => `${uid}-${name}`

const KNOWN = ['destination', 'dates', 'party_size', 'budget'] as const
type Field = (typeof KNOWN)[number]

const fields = computed<Field[]>(() =>
  KNOWN.filter((field) => props.entry.missingFields.includes(field)),
)
const hasFields = computed(() => fields.value.length > 0)
const needs = (field: Field): boolean => fields.value.includes(field)

const ctx = session.context
const draft = reactive({
  destination: ctx.destination ?? '',
  origin: ctx.origin ?? '',
  startDate: ctx.start_date ?? '',
  endDate: ctx.end_date ?? '',
  partySize: (ctx.party_size ?? null) as number | string | null,
  budgetAmount: (ctx.budget?.amount ?? null) as number | string | null,
  currency: ctx.budget?.currency ?? DEFAULT_CURRENCY,
  answer: '',
})

const num = (value: number | string | null): number | null => {
  if (value === null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const errors = computed(() => {
  const found: Partial<Record<Field | 'answer', string>> = {}
  if (needs('destination') && !draft.destination.trim()) found.destination = 'Enter a destination.'
  if (needs('dates')) {
    if (!draft.startDate || !draft.endDate) found.dates = 'Enter a start and an end date.'
    else if (draft.endDate < draft.startDate) found.dates = 'The end date must not be before the start date.'
  }
  const party = num(draft.partySize)
  if (needs('party_size') && (party === null || !Number.isInteger(party) || party < 1 || party > 50)) {
    found.party_size = 'Enter a whole number from 1 to 50.'
  }
  const amount = num(draft.budgetAmount)
  if (needs('budget')) {
    if (amount === null || amount <= 0) found.budget = 'Enter a budget above zero.'
    else if (!/^[A-Za-z]{3}$/.test(draft.currency.trim())) found.budget = 'Use a 3-letter currency code, e.g. JPY.'
  }
  if (!hasFields.value && !draft.answer.trim()) found.answer = 'Type an answer.'
  return found
})

const valid = computed(() => Object.keys(errors.value).length === 0)
const showErrors = ref(false)

function nextContext(): TripContext {
  const next: TripContext = { ...session.context }
  if (needs('destination')) {
    next.destination = draft.destination.trim()
    if (draft.origin.trim()) next.origin = draft.origin.trim()
  }
  if (needs('dates')) {
    next.start_date = draft.startDate
    next.end_date = draft.endDate
    next.days = null
  }
  if (needs('party_size')) next.party_size = num(draft.partySize)
  if (needs('budget')) {
    next.budget = { amount: num(draft.budgetAmount) ?? 0, currency: draft.currency.trim().toUpperCase() }
  }
  return next
}

async function submit(): Promise<void> {
  showErrors.value = true
  if (!valid.value || session.busy) return
  if (!hasFields.value) {
    await session.send(draft.answer)
    return
  }
  const saved = await session.saveContext(nextContext())
  if (saved) await session.send(props.entry.prompt?.trim() || 'Plan my trip')
}
</script>

<template>
  <li class="clarification-entry">
    <div class="clarification" :class="{ interactive }">
      <div class="head">
        <MessageCircleQuestion :size="16" aria-hidden="true" />
        <p class="question">{{ entry.content }}</p>
      </div>

      <form v-if="interactive" class="form" novalidate @submit.prevent="submit">
        <template v-if="hasFields">
          <div v-if="needs('destination')" class="grid">
            <div>
              <label :for="id('destination')">Destination</label>
              <input :id="id('destination')" v-model="draft.destination" type="text" maxlength="200" placeholder="e.g. Kyoto" autocomplete="off" />
            </div>
            <div>
              <label :for="id('origin')">From (optional)</label>
              <input :id="id('origin')" v-model="draft.origin" type="text" maxlength="200" placeholder="For train / flight tickets" autocomplete="off" />
            </div>
            <p v-if="showErrors && errors.destination" class="field-error span-2" role="alert">{{ errors.destination }}</p>
          </div>

          <fieldset v-if="needs('dates')" class="grid">
            <legend class="sr-only">Travel dates</legend>
            <div>
              <label :for="id('start')">Start date</label>
              <input :id="id('start')" v-model="draft.startDate" type="date" />
            </div>
            <div>
              <label :for="id('end')">End date</label>
              <input :id="id('end')" v-model="draft.endDate" type="date" :min="draft.startDate || undefined" />
            </div>
            <p v-if="showErrors && errors.dates" class="field-error span-2" role="alert">{{ errors.dates }}</p>
          </fieldset>

          <div class="grid">
            <div v-if="needs('party_size')">
              <label :for="id('party')">Party size</label>
              <input :id="id('party')" v-model.number="draft.partySize" type="number" min="1" max="50" step="1" inputmode="numeric" placeholder="Travellers" />
              <p v-if="showErrors && errors.party_size" class="field-error" role="alert">{{ errors.party_size }}</p>
            </div>
            <fieldset v-if="needs('budget')">
              <legend>Total budget</legend>
              <div class="budget">
                <input v-model.number="draft.budgetAmount" type="number" min="0" step="any" inputmode="decimal" placeholder="Amount" aria-label="Budget amount" />
                <input v-model="draft.currency" type="text" maxlength="3" aria-label="Budget currency (3-letter code)" autocomplete="off" />
              </div>
              <p v-if="showErrors && errors.budget" class="field-error" role="alert">{{ errors.budget }}</p>
            </fieldset>
          </div>
        </template>

        <div v-else>
          <label :for="id('answer')">Your answer</label>
          <input :id="id('answer')" v-model="draft.answer" type="text" maxlength="4000" autocomplete="off" />
          <p v-if="showErrors && errors.answer" class="field-error" role="alert">{{ errors.answer }}</p>
        </div>

        <div class="row">
          <button type="submit" class="btn btn-primary btn-small" :disabled="session.busy || session.savingContext">
            Continue
          </button>
        </div>
      </form>
    </div>
    <p class="status" data-testid="route-status">Needs clarification</p>
  </li>
</template>

<style scoped>
.clarification-entry {
  align-self: stretch;
}

.status {
  margin-top: 4px;
  padding-left: 6px;
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.clarification {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: var(--radius-md);
  background: var(--accent-soft);
  font-size: var(--text-sm);
}

.clarification:not(.interactive) {
  background: var(--card-soft);
  border-left-color: var(--border-strong);
}

.head {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
}

.head svg {
  flex: none;
  margin-top: 2px;
  color: var(--accent-strong);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2) var(--space-3);
}

.span-2 {
  grid-column: 1 / -1;
}

.budget {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px;
  gap: var(--space-2);
}

input {
  min-height: 36px;
  padding: 6px 10px;
}

@media (max-width: 420px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
