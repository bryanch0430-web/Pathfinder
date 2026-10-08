<script setup lang="ts">
import { computed } from 'vue'
import { Clock, Footprints, Lock, MapPin, Thermometer, TrainFront, Wallet } from 'lucide-vue-next'
import type { GeoPoint } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { estimateTravel } from '@/lib/geo'
import { formatMoney } from '@/lib/format'
import { popularTimesOf } from '@/lib/popularTimes'
import PopularTimesChart from './PopularTimesChart.vue'
import StalenessBadge from './StalenessBadge.vue'

const planStore = usePlanStore()

const item = computed(() => planStore.selectedItem)
const place = computed(() => planStore.selectedPlace)
const forecast = computed(() => planStore.selectedDay?.forecast ?? null)

const category = computed(() => {
  const value = place.value?.category ?? ''
  return value ? value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, ' ') : 'Place'
})

const price = computed(() => {
  const money = place.value?.price
  if (!money) return null
  return money.amount === 0 ? 'Free' : `${formatMoney(money.amount, money.currency)} / person`
})

const temperature = computed(() => {
  const f = forecast.value
  if (!f) return null
  return `${Math.round(f.temp_min_c)}–${Math.round(f.temp_max_c)} °C · ${f.summary}`
})

/** Travel time from the previous stop (or the hotel for the first stop), estimated client-side. */
const travel = computed(() => {
  const index = planStore.selectedItemIndex
  const here = place.value?.location
  if (index < 0 || !here) return null
  let from: GeoPoint | null | undefined
  let fromName: string
  if (index === 0) {
    from = planStore.plan?.hotel?.hotel.location
    fromName = 'the hotel'
  } else {
    const previous = planStore.selectedDay?.items?.[index - 1]
    from = previous ? planStore.placesById.get(previous.place_id)?.location : null
    fromName = previous?.title ?? 'the previous stop'
  }
  if (!from) return null
  const estimate = estimateTravel(from, here)
  return { ...estimate, fromName }
})

const popular = computed(() => popularTimesOf(place.value))
</script>

<template>
  <section class="card" aria-labelledby="place-title" aria-live="polite">
    <template v-if="item">
      <header class="head">
        <div>
          <h2 id="place-title">{{ item.title }}</h2>
          <p class="small muted">
            {{ category }}<template v-if="place?.rating"> · ★ {{ place.rating.toFixed(1) }}</template>
          </p>
        </div>
        <span v-if="item.confirmed" class="badge badge-accent"><Lock :size="11" aria-hidden="true" /> Confirmed</span>
      </header>

      <ul class="facts">
        <li>
          <span class="icon" aria-hidden="true"><Wallet :size="16" /></span>
          <div>
            <p class="label-sm">Price</p>
            <p class="value">{{ price ?? 'Not listed' }}</p>
            <StalenessBadge agent="attraction" :fetched-at="place?.source.fetched_at" :missing="!place" />
          </div>
        </li>
        <li>
          <span class="icon" aria-hidden="true"><Clock :size="16" /></span>
          <div>
            <p class="label-sm">Opening hours</p>
            <p class="value">{{ place?.opening_hours ?? 'Not listed' }}</p>
            <StalenessBadge agent="attraction" :fetched-at="place?.source.fetched_at" :missing="!place" />
          </div>
        </li>
        <li>
          <span class="icon" aria-hidden="true"><Thermometer :size="16" /></span>
          <div>
            <p class="label-sm">Temperature that day</p>
            <p class="value">{{ temperature ?? 'Unavailable' }}</p>
            <StalenessBadge agent="weather" :fetched-at="forecast?.source.fetched_at" :missing="!forecast" />
          </div>
        </li>
        <li>
          <span class="icon" aria-hidden="true">
            <Footprints v-if="travel?.mode === 'walk'" :size="16" />
            <TrainFront v-else :size="16" />
          </span>
          <div>
            <p class="label-sm">Travel time</p>
            <p class="value">
              <template v-if="travel">≈ {{ travel.minutes }} min {{ travel.mode === 'walk' ? 'walk' : 'by transit' }}</template>
              <template v-else>Unknown</template>
            </p>
            <p v-if="travel" class="tiny muted">from {{ travel.fromName }} · estimate from coordinates</p>
          </div>
        </li>
      </ul>

      <p v-if="place?.address" class="address small muted">
        <MapPin :size="13" aria-hidden="true" /> {{ place.address }}
      </p>
      <p v-if="item.needs_reservation" class="small"><span class="badge badge-warn">Reservation needed</span></p>
      <p v-if="item.note" class="small muted">{{ item.note }}</p>

      <PopularTimesChart v-if="popular" :data="popular" class="popular" />
    </template>

    <template v-else>
      <h2 id="place-title" class="sr-only">Place details</h2>
      <p class="empty">
        {{ planStore.hasPlan ? 'Select a stop on the map or in the day timeline.' : 'Place details appear here once a plan exists.' }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-3);
  margin: var(--space-4) 0 var(--space-3);
}

.facts li {
  display: flex;
  gap: var(--space-2);
  min-width: 0;
}

.icon {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--card-soft);
  border: 1px solid var(--border);
  color: var(--text);
}

.label-sm {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.value {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow-wrap: anywhere;
}

.address {
  display: flex;
  align-items: center;
  gap: 4px;
}

.popular {
  margin-top: var(--space-4);
}

@media (max-width: 420px) {
  .facts {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
