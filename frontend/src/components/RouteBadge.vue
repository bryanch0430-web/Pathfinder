<script setup lang="ts">
import { computed } from 'vue'
import type { Route } from '@/api'
import { ROUTE_LABELS } from '@/lib/constants'

const props = defineProps<{ route: Route }>()

const variant: Record<Route, string> = {
  plan: 'badge-primary',
  modify: 'badge-info',
  ask: 'badge-ok',
  unclear: 'badge-warn',
}

const label = computed(() => ROUTE_LABELS[props.route])
</script>

<template>
  <span class="badge" :class="variant[route]" :title="`Route: ${route}`">
    <span class="sr-only">Route: </span>{{ label }}
  </span>
</template>
