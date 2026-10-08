<script setup lang="ts">
import type { SectionStatus } from '@/api'

defineProps<{ status: SectionStatus }>()

const variant: Record<SectionStatus, string> = {
  ok: 'badge-ok',
  unavailable: 'badge-danger',
  stale: 'badge-warn',
}

// A symbol next to the word keeps the state readable without relying on colour alone.
const symbol: Record<SectionStatus, string> = {
  ok: '✓',
  unavailable: '✕',
  stale: '↻',
}
</script>

<template>
  <span class="badge" :class="variant[status]">
    <span aria-hidden="true">{{ symbol[status] }}</span>
    {{ status }}
  </span>
</template>
