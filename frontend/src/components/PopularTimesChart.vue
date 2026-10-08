<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import type { PopularTimes } from '@/lib/popularTimes'

/** Small hourly bar chart with a weekday dropdown. Only rendered when the data exists. */
const props = defineProps<{ data: PopularTimes }>()
const uid = useId()

const weekdays = computed(() => Object.keys(props.data))
const weekday = ref(weekdays.value[0] ?? '')
watch(weekdays, (list) => {
  if (!list.includes(weekday.value)) weekday.value = list[0] ?? ''
})

const FIRST_HOUR = 6
const bars = computed(() =>
  (props.data[weekday.value] ?? [])
    .map((value, hour) => ({ hour, value: Math.max(0, Math.min(100, value)) }))
    .filter((bar) => bar.hour >= FIRST_HOUR),
)
const peak = computed(() => bars.value.reduce((best, bar) => (bar.value > best.value ? bar : best), { hour: -1, value: -1 }))
</script>

<template>
  <div class="popular">
    <div class="head">
      <h3 :id="`${uid}-title`">Popular times</h3>
      <label class="sr-only" :for="`${uid}-day`">Weekday</label>
      <select :id="`${uid}-day`" v-model="weekday" class="day">
        <option v-for="day in weekdays" :key="day" :value="day">{{ day }}</option>
      </select>
    </div>
    <div class="bars" role="img" :aria-label="`Busiest around ${peak.hour}:00 on ${weekday}`">
      <span
        v-for="bar in bars"
        :key="bar.hour"
        class="bar"
        :class="{ peak: bar.hour === peak.hour }"
        :style="{ height: `${Math.max(4, bar.value)}%` }"
        :title="`${bar.hour}:00 · ${bar.value}%`"
      />
    </div>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.day {
  width: auto;
  min-height: 30px;
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  font-size: var(--text-sm);
}

.bars {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 64px;
}

.bar {
  flex: 1;
  border-radius: 4px 4px 0 0;
  background: var(--border-strong);
}

.bar.peak {
  background: var(--accent);
}
</style>
