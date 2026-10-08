<script setup lang="ts">
import { computed } from 'vue'
import { Sparkles } from 'lucide-vue-next'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { budgetSegments, budgetUsed } from '@/lib/budget'
import { formatMoney, formatPercent } from '@/lib/format'
import { OPTIMISE_BUDGET_MESSAGE } from '@/lib/refresh'
import DonutChart from './DonutChart.vue'

const planStore = usePlanStore()
const session = useSessionStore()

const plan = computed(() => planStore.plan)
const cost = computed(() => plan.value?.cost ?? null)
const segments = computed(() => budgetSegments(cost.value))
const used = computed(() => budgetUsed(cost.value, plan.value?.budget))

const money = (amount: number): string => formatMoney(amount, cost.value?.currency ?? plan.value?.budget?.currency ?? 'JPY')

const centerLabel = computed(() => {
  const n = plan.value?.days.length ?? 0
  return n > 0 ? `Total ${n}-Day` : 'Total'
})

const donutSegments = computed(() =>
  segments.value.map((s) => ({ key: s.key, label: s.label, value: s.amount ?? 0, color: s.color })),
)

const ariaLabel = computed(() => {
  if (!cost.value) return 'No costs yet'
  const parts = segments.value.filter((s) => s.amount !== null).map((s) => `${s.label} ${money(s.amount ?? 0)}`)
  return `${centerLabel.value} ${money(cost.value.total)}: ${parts.join(', ')}`
})

const usedClass = computed(() => {
  if (used.value === null) return ''
  if (used.value > 1) return 'badge-danger'
  if (used.value > 0.9) return 'badge-warn'
  return 'badge-ok'
})

function optimise(): void {
  void session.send(OPTIMISE_BUDGET_MESSAGE)
}
</script>

<template>
  <section class="card" aria-labelledby="budget-title">
    <header class="card-header">
      <h2 id="budget-title">Budget Details</h2>
      <span v-if="used !== null" class="badge" :class="usedClass" data-testid="budget-used">
        {{ formatPercent(used) }} used
      </span>
    </header>

    <template v-if="cost">
      <div class="body">
        <DonutChart
          :segments="donutSegments"
          :center-label="centerLabel"
          :center-value="money(cost.total)"
          :description="ariaLabel"
          :size="168"
        />
        <ul class="legend">
          <li v-for="segment in segments" :key="segment.key" :class="{ untracked: segment.amount === null }">
            <span class="swatch" :style="{ background: segment.color }" aria-hidden="true" />
            <span class="name">{{ segment.label }}</span>
            <span class="amount">{{ segment.amount === null ? 'not tracked' : money(segment.amount) }}</span>
          </li>
        </ul>
      </div>
      <p v-if="plan?.budget" class="tiny muted">
        Budget {{ formatMoney(plan.budget.amount, plan.budget.currency) }}<template v-if="used === null"> · different currency, not compared</template>
      </p>
      <p v-if="!cost.complete" class="tiny warn">
        Incomplete: a priced section is unavailable or priced in another currency.
      </p>
    </template>
    <p v-else class="empty">Costs appear once a plan exists.</p>

    <button
      type="button"
      class="btn btn-primary btn-block optimise"
      :disabled="!plan || session.busy"
      @click="optimise"
    >
      <Sparkles :size="16" aria-hidden="true" />
      Optimise my budget
    </button>
  </section>
</template>

<style scoped>
.body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  margin-bottom: var(--space-3);
}

.legend {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  min-width: 150px;
  font-size: var(--text-sm);
}

.legend li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.swatch {
  flex: none;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.amount {
  margin-left: auto;
  font-weight: var(--weight-medium);
}

.untracked .name,
.untracked .amount {
  color: var(--text-muted);
  font-weight: normal;
}

.untracked .swatch {
  background: transparent !important;
  border: 1.5px dashed var(--icon-muted);
}

.warn {
  margin-top: 4px;
  color: var(--warn);
}

.optimise {
  margin-top: var(--space-4);
}
</style>
