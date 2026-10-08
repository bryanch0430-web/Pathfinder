<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { GeoPoint } from '@/api'
import { usePlanStore } from '@/stores/plan'
import MapControls from './MapControls.vue'

/**
 * TODO(provisional): provider-agnostic map placeholder. The proposal's map provider (Google
 * Maps, AMap fallback) is provisional, so this draws the selected day's stops as numbered pins
 * on a plain SVG with an equirectangular projection from TripPlan coordinates. It needs no tiles
 * and no key, so it works offline. Swap the <svg> for a real map component behind the same
 * inputs (pins + selection) when a provider is chosen.
 */
const planStore = usePlanStore()

const WIDTH = 640
const HEIGHT = 400
const PADDING = 56
const ZOOM_STEPS = [1, 1.5, 2.25, 3.4] as const
const GRID = 40

interface Pin {
  key: string
  kind: 'hotel' | 'stop'
  order: number
  label: string
  x: number
  y: number
}

const zoomIndex = ref(0)
const routeLayer = ref(true)
const zoom = computed(() => ZOOM_STEPS[zoomIndex.value] ?? 1)

const day = computed(() => planStore.selectedDay)
const hotel = computed(() => planStore.plan?.hotel?.hotel ?? null)

const raw = computed(() => {
  const list: Array<Omit<Pin, 'x' | 'y'> & { point: GeoPoint }> = []
  if (hotel.value?.location) {
    list.push({ key: 'hotel', kind: 'hotel', order: 0, label: hotel.value.name, point: hotel.value.location })
  }
  for (const [index, item] of (day.value?.items ?? []).entries()) {
    const point = planStore.placesById.get(item.place_id)?.location
    if (point) list.push({ key: item.item_id, kind: 'stop', order: index + 1, label: item.title, point })
  }
  return list
})

const pins = computed<Pin[]>(() => {
  const list = raw.value
  if (list.length === 0) return []
  const meanLat = list.reduce((sum, p) => sum + p.point.lat, 0) / list.length
  const lngScale = Math.cos((meanLat * Math.PI) / 180)
  const xs = list.map((p) => p.point.lng * lngScale)
  const ys = list.map((p) => -p.point.lat)
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const spanX = Math.max(...xs) - minX
  const spanY = Math.max(...ys) - minY
  const scale = Math.min(
    (WIDTH - 2 * PADDING) / Math.max(spanX, 1e-9),
    (HEIGHT - 2 * PADDING) / Math.max(spanY, 1e-9),
  )
  const safeScale = Number.isFinite(scale) && spanX + spanY > 1e-9 ? scale : 0
  const offsetX = (WIDTH - spanX * safeScale) / 2
  const offsetY = (HEIGHT - spanY * safeScale) / 2
  return list.map((p, i) => ({
    key: p.key,
    kind: p.kind,
    order: p.order,
    label: p.label,
    x: offsetX + ((xs[i] ?? 0) - minX) * safeScale,
    y: offsetY + ((ys[i] ?? 0) - minY) * safeScale,
  }))
})

const stops = computed(() => pins.value.filter((p) => p.kind === 'stop'))
const hotelPin = computed(() => (routeLayer.value ? pins.value.find((p) => p.kind === 'hotel') ?? null : null))
const unplotted = computed(() => (day.value?.items?.length ?? 0) - stops.value.length)

const route = computed(() =>
  [hotelPin.value, ...stops.value]
    .filter((p): p is Pin => p !== null)
    .map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' '),
)

/** Zoom keeps the selected stop (or the centre) in view. */
const viewBox = computed(() => {
  const w = WIDTH / zoom.value
  const h = HEIGHT / zoom.value
  const focus = stops.value.find((p) => p.key === planStore.selectedItemId)
  const fx = focus?.x ?? WIDTH / 2
  const fy = focus?.y ?? HEIGHT / 2
  const x = Math.min(Math.max(fx - w / 2, 0), WIDTH - w)
  const y = Math.min(Math.max(fy - h / 2, 0), HEIGHT - h)
  return `${x.toFixed(1)} ${y.toFixed(1)} ${w.toFixed(1)} ${h.toFixed(1)}`
})

/** Pins keep their on-screen size while zooming. */
const k = computed(() => 1 / zoom.value)

watch(() => planStore.selectedDayIndex, () => (zoomIndex.value = 0))

function select(pin: Pin): void {
  if (pin.kind === 'stop') planStore.selectItem(pin.key)
}

function onPinKey(event: KeyboardEvent, pin: Pin): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    select(pin)
  }
}

const description = computed(() => {
  const n = stops.value.length
  const dayNumber = planStore.selectedDayIndex + 1
  return `Map of day ${dayNumber}: ${n} numbered ${n === 1 ? 'stop' : 'stops'}${hotelPin.value ? ' and the hotel' : ''}, in visiting order.`
})
</script>

<template>
  <section class="card map-card" aria-labelledby="map-title">
    <h2 id="map-title" class="sr-only">Map</h2>
    <div class="map">
      <MapControls
        :can-zoom-in="zoomIndex < ZOOM_STEPS.length - 1"
        :can-zoom-out="zoomIndex > 0"
        :route-layer="routeLayer"
        :can-close="planStore.selectedItemId !== null"
        @zoom-in="zoomIndex++"
        @zoom-out="zoomIndex--"
        @toggle-layers="routeLayer = !routeLayer"
        @close="planStore.selectItem(null)"
      />

      <svg :viewBox="viewBox" preserveAspectRatio="xMidYMid meet" role="group" :aria-label="description">
        <defs>
          <pattern id="map-grid" :width="GRID" :height="GRID" patternUnits="userSpaceOnUse">
            <path :d="`M ${GRID} 0 L 0 0 0 ${GRID}`" fill="none" stroke="var(--map-grid)" stroke-width="1" />
          </pattern>
        </defs>
        <rect x="0" y="0" :width="WIDTH" :height="HEIGHT" fill="var(--map-land)" />
        <rect x="0" y="0" :width="WIDTH" :height="HEIGHT" fill="url(#map-grid)" />

        <polyline
          v-if="routeLayer && route"
          :points="route"
          fill="none"
          stroke="var(--map-route)"
          :stroke-width="2 * k"
          :stroke-dasharray="`${6 * k} ${5 * k}`"
          stroke-linecap="round"
        />

        <g v-if="hotelPin" :transform="`translate(${hotelPin.x} ${hotelPin.y})`" aria-hidden="true">
          <rect :x="-14 * k" :y="-14 * k" :width="28 * k" :height="28 * k" :rx="8 * k" fill="var(--primary)" />
          <text :font-size="13 * k" font-weight="600" text-anchor="middle" dominant-baseline="central" fill="var(--primary-text)">H</text>
        </g>

        <g
          v-for="pin in stops"
          :key="pin.key"
          class="pin"
          :class="{ selected: pin.key === planStore.selectedItemId }"
          :transform="`translate(${pin.x} ${pin.y})`"
          role="button"
          tabindex="0"
          :aria-label="`Stop ${pin.order}: ${pin.label}`"
          :aria-pressed="pin.key === planStore.selectedItemId"
          @click="select(pin)"
          @keydown="onPinKey($event, pin)"
        >
          <circle v-if="pin.key === planStore.selectedItemId" :r="23 * k" fill="none" stroke="var(--accent)" :stroke-width="4 * k" />
          <circle :r="17 * k" fill="var(--card)" stroke="var(--primary)" :stroke-width="2 * k" />
          <text :font-size="14 * k" font-weight="600" text-anchor="middle" dominant-baseline="central" fill="var(--text)">
            {{ pin.order }}
          </text>
          <title>{{ pin.label }}</title>
        </g>
      </svg>

      <p v-if="!planStore.hasPlan" class="overlay small muted">Your route appears here once a plan exists.</p>
      <p v-else-if="stops.length === 0" class="overlay small muted">No stops with coordinates on this day.</p>
    </div>
    <p v-if="unplotted > 0" class="tiny muted note">
      {{ unplotted }} {{ unplotted === 1 ? 'stop has' : 'stops have' }} no coordinates and {{ unplotted === 1 ? 'is' : 'are' }} not drawn.
    </p>
  </section>
</template>

<style scoped>
.map-card {
  padding: var(--space-2);
}

.map {
  position: relative;
  overflow: hidden;
  border-radius: calc(var(--radius-card) - 6px);
  width: 100%;
  aspect-ratio: 16 / 10;
  background: var(--map-land);
}

svg {
  display: block;
  width: 100%;
  height: 100%;
}

.pin {
  cursor: pointer;
  outline: none;
}

.pin:focus-visible circle:last-of-type {
  stroke: var(--focus);
}

.overlay {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: var(--space-8);
  text-align: center;
  pointer-events: none;
}

.note {
  padding: var(--space-2) var(--space-3) var(--space-1);
}
</style>
