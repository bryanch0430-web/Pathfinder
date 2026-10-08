<script setup lang="ts">
import { computed } from 'vue'
import { BedDouble, CloudSun, Landmark, Ticket } from 'lucide-vue-next'

/** Outlined chips that put a starter sentence into the chat input (they never send by themselves). */
const props = defineProps<{ destination?: string | null; disabled?: boolean }>()
const emit = defineEmits<{ insert: [text: string] }>()

const chips = computed(() => {
  const place = props.destination?.trim() || 'my destination'
  return [
    { label: 'Hotel', icon: BedDouble, text: 'Swap the hotel for something cheaper' },
    { label: 'Tickets', icon: Ticket, text: 'Re-check the train and flight tickets' },
    { label: 'Attractions', icon: Landmark, text: 'Add a museum on day 2' },
    { label: 'Weather', icon: CloudSun, text: `What is the weather in ${place}?` },
  ]
})
</script>

<template>
  <div class="chips" role="group" aria-label="Quick starters">
    <button
      v-for="chip in chips"
      :key="chip.label"
      type="button"
      class="chip"
      :disabled="disabled"
      :title="chip.text"
      @click="emit('insert', chip.text)"
    >
      <component :is="chip.icon" :size="14" aria-hidden="true" />
      {{ chip.label }}
    </button>
  </div>
</template>

<style scoped>
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
