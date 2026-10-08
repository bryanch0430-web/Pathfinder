<script setup lang="ts">
import { computed } from 'vue'

/** SVG donut. Segments with a zero or missing value are skipped; the centre shows two lines. */
interface DonutSegment {
  key: string
  label: string
  value: number
  color: string
}

const props = withDefaults(
  defineProps<{ segments: DonutSegment[]; centerLabel: string; centerValue: string; size?: number; description: string }>(),
  { size: 180 },
)

const STROKE = 18
const GAP = 2 // gap between segments, in the same units as the circumference
const radius = computed(() => (props.size - STROKE) / 2)
const circumference = computed(() => 2 * Math.PI * radius.value)

const arcs = computed(() => {
  const visible = props.segments.filter((s) => s.value > 0)
  const total = visible.reduce((sum, s) => sum + s.value, 0)
  if (total <= 0) return []
  let offset = 0
  return visible.map((s) => {
    const length = (s.value / total) * circumference.value
    const arc = {
      key: s.key,
      color: s.color,
      dash: `${Math.max(0, length - (visible.length > 1 ? GAP : 0))} ${circumference.value}`,
      offset: -offset,
    }
    offset += length
    return arc
  })
})
</script>

<template>
  <svg :width="size" :height="size" :viewBox="`0 0 ${size} ${size}`" role="img" :aria-label="description" class="donut">
    <g :transform="`rotate(-90 ${size / 2} ${size / 2})`">
      <circle :cx="size / 2" :cy="size / 2" :r="radius" fill="none" stroke="var(--card-soft)" :stroke-width="STROKE" />
      <circle
        v-for="arc in arcs"
        :key="arc.key"
        :cx="size / 2"
        :cy="size / 2"
        :r="radius"
        fill="none"
        :stroke="arc.color"
        :stroke-width="STROKE"
        :stroke-dasharray="arc.dash"
        :stroke-dashoffset="arc.offset"
      />
    </g>
    <text :x="size / 2" :y="size / 2 - 10" text-anchor="middle" class="c-label">{{ centerLabel }}</text>
    <text :x="size / 2" :y="size / 2 + 14" text-anchor="middle" class="c-value">{{ centerValue }}</text>
  </svg>
</template>

<style scoped>
.donut {
  display: block;
  max-width: 100%;
  height: auto;
}

.c-label {
  font-size: 12px;
  fill: var(--text-muted);
}

.c-value {
  font-size: 18px;
  font-weight: 600;
  fill: var(--text);
}
</style>
