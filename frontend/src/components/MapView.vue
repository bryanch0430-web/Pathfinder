<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import type { GeoPoint } from '@/api'
import { useSessionStore } from '@/stores/session'
import { formatDate, formatShortDate, formatTimeRange } from '@/lib/format'

/**
 * PLACEHOLDER for the proposal's "map replay". It draws the selected day's stops on a plain SVG
 * canvas (equirectangular projection, no basemap) so the data path from plan.places to a visual
 * is in place. Real map tiles and the replay animation are a later step.
 */

const store = useSessionStore()
const uid = useId()

const WIDTH = 480
const HEIGHT = 320
const PADDING = 40
const MAX_LABEL = 22

interface Marker {
  key: string
  kind: 'hotel' | 'stop'
  /** 1-based visiting order for stops; 0 for the hotel. */
  order: number
  label: string
  point: GeoPoint
  x: number
  y: number
}

const selectedIndex = ref(0)
const days = computed(() => store.plan?.days ?? [])

// Keep the selection valid when a new plan has fewer days.
watch(days, (list) => {
  if (selectedIndex.value >= list.length) selectedIndex.value = 0
})

const day = computed(() => days.value[selectedIndex.value] ?? null)
const placesById = computed(() => new Map((store.plan?.places ?? []).map((place) => [place.place_id, place])))

interface RawMarker {
  key: string
  kind: 'hotel' | 'stop'
  order: number
  label: string
  point: GeoPoint
}

const rawMarkers = computed<RawMarker[]>(() => {
  const list: RawMarker[] = []
  const hotelPoint = store.plan?.hotel?.hotel.location
  if (hotelPoint) {
    list.push({
      key: 'hotel',
      kind: 'hotel',
      order: 0,
      label: store.plan?.hotel?.hotel.name ?? 'Hotel',
      point: hotelPoint,
    })
  }
  const items = day.value?.items ?? []
  for (const [index, item] of items.entries()) {
    const point = placesById.value.get(item.place_id)?.location
    if (point) list.push({ key: item.item_id, kind: 'stop', order: index + 1, label: item.title, point })
  }
  return list
})

/** Items of the selected day that cannot be drawn because their place has no coordinates. */
const unplotted = computed(() =>
  (day.value?.items ?? []).filter((item) => !placesById.value.get(item.place_id)?.location),
)

const markers = computed<Marker[]>(() => {
  const raw = rawMarkers.value
  if (raw.length === 0) return []

  // Equirectangular projection; scale longitude by cos(latitude) so distances look right.
  const meanLat = raw.reduce((sum, m) => sum + m.point.lat, 0) / raw.length
  const lngScale = Math.cos((meanLat * Math.PI) / 180)
  const xs = raw.map((m) => m.point.lng * lngScale)
  const ys = raw.map((m) => -m.point.lat)
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const spanX = Math.max(...xs) - minX
  const spanY = Math.max(...ys) - minY

  const innerW = WIDTH - 2 * PADDING
  const innerH = HEIGHT - 2 * PADDING
  const scale =
    spanX < 1e-9 && spanY < 1e-9 ? 1 : Math.min(innerW / Math.max(spanX, 1e-9), innerH / Math.max(spanY, 1e-9))
  const offsetX = (WIDTH - spanX * scale) / 2
  const offsetY = (HEIGHT - spanY * scale) / 2

  return raw.map((m, i) => ({
    ...m,
    x: offsetX + ((xs[i] ?? 0) - minX) * scale,
    y: offsetY + ((ys[i] ?? 0) - minY) * scale,
  }))
})

const hotelMarker = computed(() => markers.value.find((m) => m.kind === 'hotel') ?? null)
const stopMarkers = computed(() => markers.value.filter((m) => m.kind === 'stop'))

/** Hotel -> stop 1 -> stop 2 -> ... in visiting order. */
const routePoints = computed(() =>
  [hotelMarker.value, ...stopMarkers.value]
    .filter((m): m is Marker => m !== null)
    .map((m) => `${m.x.toFixed(1)},${m.y.toFixed(1)}`)
    .join(' '),
)

/** Dashed leg from the last stop back to the hotel. */
const returnLeg = computed(() => {
  const hotel = hotelMarker.value
  const last = stopMarkers.value[stopMarkers.value.length - 1]
  if (!hotel || !last || stopMarkers.value.length < 1) return null
  return { x1: last.x, y1: last.y, x2: hotel.x, y2: hotel.y }
})

const truncate = (text: string): string => (text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text)

const summary = computed(() => {
  const stops = stopMarkers.value.length
  const hotel = hotelMarker.value ? ' plus the hotel' : ''
  return `Schematic route for ${day.value ? formatDate(day.value.date) : 'the selected day'}: ${stops} plotted ${stops === 1 ? 'stop' : 'stops'}${hotel}, in visiting order.`
})

function coords(point: GeoPoint): string {
  return `${point.lat.toFixed(4)}, ${point.lng.toFixed(4)}`
}
</script>

<template>
  <section class="card" aria-labelledby="map-title">
    <header>
      <h2 id="map-title">Day route</h2>
      <span class="badge" title="Real map tiles and replay are a later step">Placeholder</span>
    </header>

    <p v-if="!store.plan" class="empty">The route preview appears once there is a plan.</p>
    <p v-else-if="days.length === 0" class="empty">This plan has no days to draw.</p>

    <div v-else class="stack">
      <div class="selector">
        <label :for="`${uid}-day`">Day</label>
        <select :id="`${uid}-day`" v-model.number="selectedIndex">
          <option v-for="(d, index) in days" :key="d.date" :value="index">
            Day {{ index + 1 }} ({{ formatShortDate(d.date) }})
          </option>
        </select>
      </div>

      <p v-if="markers.length === 0" class="empty">
        None of this day's stops have coordinates, so there is nothing to draw.
      </p>

      <svg
        v-else
        class="canvas"
        :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
        role="img"
        :aria-labelledby="`${uid}-svg-title`"
        preserveAspectRatio="xMidYMid meet"
      >
        <title :id="`${uid}-svg-title`">{{ summary }}</title>
        <rect class="bg" x="0" y="0" :width="WIDTH" :height="HEIGHT" rx="8" />
        <g class="grid" aria-hidden="true">
          <line v-for="n in 5" :key="`v${n}`" :x1="(WIDTH / 6) * n" y1="0" :x2="(WIDTH / 6) * n" :y2="HEIGHT" />
          <line v-for="n in 3" :key="`h${n}`" x1="0" :y1="(HEIGHT / 4) * n" :x2="WIDTH" :y2="(HEIGHT / 4) * n" />
        </g>

        <line
          v-if="returnLeg"
          class="leg-return"
          :x1="returnLeg.x1"
          :y1="returnLeg.y1"
          :x2="returnLeg.x2"
          :y2="returnLeg.y2"
        />
        <polyline v-if="stopMarkers.length > 0" class="route" :points="routePoints" />

        <g v-for="marker in markers" :key="marker.key">
          <title>{{ marker.kind === 'hotel' ? 'Hotel: ' : `Stop ${marker.order}: ` }}{{ marker.label }} ({{ coords(marker.point) }})</title>
          <template v-if="marker.kind === 'hotel'">
            <rect class="hotel-mark" :x="marker.x - 11" :y="marker.y - 11" width="22" height="22" rx="5" />
            <text class="mark-text" :x="marker.x" :y="marker.y + 4" text-anchor="middle">H</text>
          </template>
          <template v-else>
            <circle class="stop-mark" :cx="marker.x" :cy="marker.y" r="12" />
            <text class="mark-text" :x="marker.x" :y="marker.y + 4" text-anchor="middle">{{ marker.order }}</text>
          </template>
          <text class="label" :x="marker.x" :y="marker.y - 18" text-anchor="middle">{{ truncate(marker.label) }}</text>
        </g>
      </svg>

      <div>
        <h3 class="list-title">Visiting order</h3>
        <ol class="order">
          <li v-if="store.plan.hotel">
            <strong>Hotel:</strong> {{ store.plan.hotel.hotel.name }}
            <span v-if="store.plan.hotel.hotel.location" class="muted small">
              ({{ coords(store.plan.hotel.hotel.location) }})
            </span>
            <span v-else class="muted small">(no coordinates)</span>
          </li>
          <li v-for="(item, index) in day?.items ?? []" :key="item.item_id">
            <strong>{{ index + 1 }}.</strong>
            <span v-if="formatTimeRange(item.start_time, item.end_time)" class="muted">
              {{ formatTimeRange(item.start_time, item.end_time) }}
            </span>
            {{ item.title }}
            <span v-if="placesById.get(item.place_id)?.location" class="muted small">
              ({{ coords(placesById.get(item.place_id)!.location!) }})
            </span>
            <span v-else class="muted small">(no coordinates)</span>
          </li>
        </ol>
        <p v-if="unplotted.length > 0" class="small muted">
          {{ unplotted.length }} {{ unplotted.length === 1 ? 'stop is' : 'stops are' }} not drawn because the place has no coordinates.
        </p>
      </div>

      <p class="small muted note">
        Placeholder preview: points are projected from latitude/longitude without a basemap. Real map
        tiles and the route replay are a later step.
      </p>
    </div>
  </section>
</template>

<style scoped>
.selector {
  max-width: 260px;
}

.canvas {
  width: 100%;
  height: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.bg {
  fill: var(--surface-2);
}

.grid line {
  stroke: var(--border);
  stroke-width: 1;
  stroke-dasharray: 2 6;
}

.route {
  fill: none;
  stroke: var(--primary);
  stroke-width: 3;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.leg-return {
  stroke: var(--muted);
  stroke-width: 2;
  stroke-dasharray: 6 5;
}

.stop-mark {
  fill: var(--primary);
  stroke: var(--surface);
  stroke-width: 2;
}

.hotel-mark {
  fill: var(--ok);
  stroke: var(--surface);
  stroke-width: 2;
}

.mark-text {
  fill: var(--primary-text);
  font-size: 12px;
  font-weight: 700;
}

.hotel-mark + .mark-text {
  fill: var(--surface);
}

.label {
  fill: var(--text);
  stroke: var(--surface);
  stroke-width: 3px;
  paint-order: stroke;
  font-size: 11px;
}

.list-title {
  margin-bottom: 6px;
}

.order {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.order li {
  overflow-wrap: anywhere;
}
</style>
