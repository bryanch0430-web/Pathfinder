<script setup lang="ts">
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import type { AgentName } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { formatDateTime } from '@/lib/format'
import { REFRESH_MESSAGES } from '@/lib/refresh'
import { AGENT_LABELS } from '@/lib/constants'

/**
 * Timestamp caption for one tool-sourced fact. When the owning section is stale or unavailable,
 * or the fact itself is missing, it adds a grey "unavailable – refresh" badge that sends the
 * section back through the modify path (only that agent re-runs).
 */
const props = defineProps<{
  agent: AgentName
  /** When this fact was fetched; falls back to the section's timestamp. */
  fetchedAt?: string | null
  /** The fact is absent from the plan even though the section may be OK. */
  missing?: boolean
}>()

const planStore = usePlanStore()
const session = useSessionStore()

const section = computed(() => planStore.sectionsByAgent.get(props.agent) ?? null)

const state = computed<'ok' | 'stale' | 'unavailable'>(() => {
  if (props.missing || section.value?.status === 'unavailable') return 'unavailable'
  if (section.value?.status === 'stale') return 'stale'
  return 'ok'
})

const timestamp = computed(() => props.fetchedAt ?? section.value?.fetched_at ?? null)

function refresh(): void {
  void session.send(REFRESH_MESSAGES[props.agent])
}
</script>

<template>
  <span class="staleness">
    <span v-if="timestamp && state !== 'unavailable'" class="caption">
      Updated {{ formatDateTime(timestamp) }}
    </span>
    <button
      v-if="state !== 'ok'"
      type="button"
      class="refresh"
      :disabled="session.busy"
      :aria-label="`${AGENT_LABELS[agent]} data is ${state}. Refresh it`"
      :title="section?.reason ?? undefined"
      @click="refresh"
    >
      <RefreshCw :size="11" aria-hidden="true" />
      {{ state }} – refresh
    </button>
  </span>
</template>

<style scoped>
.staleness {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.caption {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.refresh {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-pill);
  background: var(--unavailable-soft);
  color: var(--unavailable);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.refresh:hover:not(:disabled) {
  border-color: var(--icon-muted);
}

.refresh:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
