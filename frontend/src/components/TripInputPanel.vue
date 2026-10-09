<script setup lang="ts">
import { computed, watch } from 'vue'
import { Pencil } from 'lucide-vue-next'
import type { TripContext } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import { contextSummary } from '@/lib/context'
import TripForm from './TripForm.vue'

/**
 * Trip input panel (left column). Expanded until a plan exists, then a one-line summary with an
 * Edit button. Generate saves the context and sends "Plan my trip" through the normal chat path,
 * the same flow the clarification card uses.
 */
const GENERATE_MESSAGE = 'Plan my trip'

const planStore = usePlanStore()
const session = useSessionStore()
const ui = useUiStore()

const expanded = computed(() => !planStore.hasPlan || ui.inputPanelExpanded)

/** The saved context; a plan made from chat alone may not have filled it, so the plan fills gaps. */
const summary = computed(() => {
  const plan = planStore.plan
  const fromPlan: TripContext = plan
    ? { destination: plan.destination, start_date: plan.start_date, end_date: plan.end_date, party_size: plan.party_size, budget: plan.budget }
    : {}
  const context = session.context
  const merged: TripContext = context.destination ? context : { ...fromPlan, ...withoutEmpty(context) }
  return contextSummary(merged) || 'No trip details yet'
})

function withoutEmpty(context: TripContext): TripContext {
  return Object.fromEntries(Object.entries(context).filter(([, value]) => value != null)) as TripContext
}

// A plan arriving (from Generate or from chat) folds the panel to its summary.
watch(
  () => planStore.hasPlan,
  (hasPlan) => {
    if (hasPlan) ui.collapseInputPanel()
  },
  { immediate: true },
)

async function generate(save: () => Promise<boolean>): Promise<void> {
  if (session.busy || session.savingContext) return
  if (!(await save())) return
  const result = await session.send(GENERATE_MESSAGE)
  if (!result.ok) return
  ui.collapseInputPanel()
  // No plan means the reply is a question back (or an answer); it is in the chat, so show it.
  if (!planStore.hasPlan) ui.setLeftPanel('chat')
}
</script>

<template>
  <section class="card trip-input" aria-labelledby="trip-input-title">
    <header class="head">
      <h2 id="trip-input-title">Your trip</h2>
      <button
        v-if="expanded && planStore.hasPlan"
        type="button"
        class="btn btn-small"
        aria-controls="trip-input-body"
        aria-expanded="true"
        @click="ui.collapseInputPanel()"
      >
        Hide
      </button>
    </header>

    <div id="trip-input-body">
      <div v-if="!expanded" class="summary">
        <p class="summary-text" data-testid="trip-summary" :title="summary">{{ summary }}</p>
        <button
          type="button"
          class="btn btn-small"
          aria-controls="trip-input-body"
          aria-expanded="false"
          @click="ui.expandInputPanel()"
        >
          <Pencil :size="14" aria-hidden="true" />
          Edit
        </button>
      </div>

      <TripForm v-else>
        <template #actions="{ save, revert, hasErrors, dirty }">
          <button
            type="button"
            class="btn btn-primary"
            data-testid="generate"
            :disabled="session.busy || session.savingContext || hasErrors"
            @click="generate(save)"
          >
            {{ session.savingContext ? 'Saving...' : session.busy ? 'Working...' : 'Generate' }}
          </button>
          <button type="button" class="btn" :disabled="!dirty || session.savingContext" @click="revert">
            Discard edits
          </button>
        </template>
      </TripForm>
    </div>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.summary {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.summary-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: var(--weight-medium);
}
</style>
