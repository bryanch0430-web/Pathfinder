<script setup lang="ts">
import { computed, reactive } from 'vue'
import { Lock, LockOpen } from 'lucide-vue-next'
import type { TicketOption } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { formatDate, formatDateTime, formatMoneyValue, nightsBetween } from '@/lib/format'
import { lockError, onSelectableKeydown } from './selectable'

/** Hotel and tickets cards. Each is selectable; the selected one offers lock/unlock. */
const planStore = usePlanStore()
const session = useSessionStore()

const plan = computed(() => planStore.plan)
const hotel = computed(() => plan.value?.hotel ?? null)
/** Inline lock errors by part: 'hotel' or a ticket id. */
const errors = reactive<Record<string, string>>({})

const isSelected = (kind: 'hotel' | 'ticket', id: string): boolean =>
  planStore.selection?.kind === kind && planStore.selection.id === id

const hotelSelected = computed(() => !!hotel.value && isSelected('hotel', hotel.value.hotel.hotel_id))

function toggleHotel(): void {
  if (hotel.value) planStore.select('hotel', hotel.value.hotel.hotel_id)
}

async function lock(key: string, apply: () => Promise<boolean>): Promise<void> {
  delete errors[key]
  const message = await lockError(apply)
  if (message) errors[key] = message
}

function ticketStatusClass(ticket: TicketOption): string {
  if (ticket.status === 'cancelled') return 'badge-danger'
  if (ticket.status === 'delayed') return 'badge-warn'
  return 'badge-ok'
}

function ticketStatusText(ticket: TicketOption): string {
  if (ticket.status === 'delayed') return `delayed ${ticket.delay_minutes} min`
  return ticket.status
}
</script>

<template>
  <div v-if="plan" class="bookings">
    <section aria-labelledby="ws-hotel-title">
      <h3 id="ws-hotel-title">Hotel</h3>
      <div v-if="hotel" class="subcard part" :class="{ selected: hotelSelected }" data-testid="hotel">
        <div
          role="button"
          tabindex="0"
          class="part-main"
          :aria-pressed="hotelSelected"
          @click="toggleHotel"
          @keydown="onSelectableKeydown($event, toggleHotel)"
        >
          <p>
            <strong>{{ hotel.hotel.name }}</strong>
            <span v-if="hotel.confirmed" class="badge badge-ok"><Lock :size="11" aria-hidden="true" /> Locked</span>
          </p>
          <p class="small muted">
            {{ formatDate(hotel.check_in) }} to {{ formatDate(hotel.check_out) }}
            ({{ nightsBetween(hotel.check_in, hotel.check_out) }} nights),
            {{ formatMoneyValue(hotel.hotel.nightly_price) }} per night
          </p>
          <p class="badges">
            <span v-if="hotel.hotel.style" class="badge">{{ hotel.hotel.style }}</span>
            <span v-if="hotel.hotel.rating != null" class="badge">Rating {{ hotel.hotel.rating }} / 5</span>
            <span v-if="hotel.hotel.distance_km != null" class="badge">{{ hotel.hotel.distance_km }} km from the centre</span>
          </p>
          <p v-if="hotel.hotel.address" class="small muted">{{ hotel.hotel.address }}</p>
        </div>
        <div v-if="hotelSelected" class="controls">
          <button
            type="button"
            class="btn btn-small"
            :aria-pressed="hotel.confirmed"
            :aria-label="hotel.confirmed ? `Unlock hotel ${hotel.hotel.name}` : `Lock hotel ${hotel.hotel.name} (keep it across edits)`"
            :disabled="session.busy || session.confirming"
            @click="lock('hotel', () => session.setHotelConfirmed(!hotel!.confirmed))"
          >
            <Lock v-if="hotel.confirmed" :size="14" aria-hidden="true" />
            <LockOpen v-else :size="14" aria-hidden="true" />
            {{ hotel.confirmed ? 'Unlock' : 'Lock' }}
          </button>
          <p v-if="errors.hotel" class="field-error" role="alert">{{ errors.hotel }}</p>
        </div>
      </div>
      <p v-else class="small muted">No hotel in this plan.</p>
    </section>

    <section aria-labelledby="ws-tickets-title">
      <h3 id="ws-tickets-title">Tickets</h3>
      <p v-if="(plan.tickets ?? []).length === 0" class="small muted">
        No tickets in this plan{{ plan.origin ? '' : ' (add an origin to get ticket options)' }}.
      </p>
      <ul v-else class="tickets">
        <li
          v-for="ticket in plan.tickets"
          :key="ticket.ticket_id"
          class="subcard part"
          :class="{ selected: isSelected('ticket', ticket.ticket_id) }"
          data-testid="ticket"
        >
          <div
            role="button"
            tabindex="0"
            class="part-main"
            :aria-pressed="isSelected('ticket', ticket.ticket_id)"
            @click="planStore.select('ticket', ticket.ticket_id)"
            @keydown="onSelectableKeydown($event, () => planStore.select('ticket', ticket.ticket_id))"
          >
            <p>
              <span class="badge badge-accent">{{ ticket.direction }}</span>
              <strong>{{ ticket.carrier }}</strong> ({{ ticket.mode }})
              <span class="badge" :class="ticketStatusClass(ticket)">{{ ticketStatusText(ticket) }}</span>
              <span v-if="ticket.confirmed" class="badge badge-ok"><Lock :size="11" aria-hidden="true" /> Locked</span>
            </p>
            <p>{{ ticket.origin }} → {{ ticket.destination }}</p>
            <p class="small muted">
              Departs {{ formatDateTime(ticket.depart_at) }}, arrives {{ formatDateTime(ticket.arrive_at) }}
              <span aria-hidden="true">&middot;</span> {{ formatMoneyValue(ticket.price) }}
            </p>
          </div>
          <div v-if="isSelected('ticket', ticket.ticket_id)" class="controls">
            <button
              type="button"
              class="btn btn-small"
              :aria-pressed="ticket.confirmed"
              :aria-label="`${ticket.confirmed ? 'Unlock' : 'Lock'} ${ticket.direction} ticket ${ticket.origin} to ${ticket.destination}`"
              :disabled="session.busy || session.confirming"
              @click="lock(ticket.ticket_id, () => session.setTicketConfirmed(ticket.ticket_id, !ticket.confirmed))"
            >
              <Lock v-if="ticket.confirmed" :size="14" aria-hidden="true" />
              <LockOpen v-else :size="14" aria-hidden="true" />
              {{ ticket.confirmed ? 'Unlock' : 'Lock' }}
            </button>
            <p v-if="errors[ticket.ticket_id]" class="field-error" role="alert">{{ errors[ticket.ticket_id] }}</p>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.bookings {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
  gap: var(--space-4);
  align-items: start;
}

h3 {
  margin-bottom: var(--space-2);
}

.part-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-wrap: anywhere;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tickets {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.controls {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--border);
}
</style>
