/**
 * Plan store: the current TripPlan plus what the user has selected in it (day and stop).
 *
 * The session store writes the plan here after every turn; the dashboard cards only read it.
 * Selecting a day updates the map, the timeline and the place card together because all three
 * read `selectedDay` / `selectedItem` from this one store.
 *
 * `selection` is the workspace selection (a day, stop, hotel or ticket) that scopes the next chat
 * message as its `focus`. Selecting a day or stop also moves `selectedDayIndex`/`selectedItemId`.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  AgentName,
  DayPlan,
  FocusKind,
  ItineraryItem,
  Place,
  PlanFocus,
  SectionState,
  TripPlan,
} from '@/api'

/** One selected part of the plan: a day (by date), a stop, the hotel or a ticket. */
export type PlanSelection = PlanFocus

/** Index of the day holding the stop, or -1. */
function dayIndexOfItem(plan: TripPlan, itemId: string): number {
  return plan.days.findIndex((day) => day.items?.some((item) => item.item_id === itemId))
}

function selectionExists(plan: TripPlan, selection: PlanSelection): boolean {
  switch (selection.kind) {
    case 'day':
      return plan.days.some((day) => day.date === selection.id)
    case 'item':
      return dayIndexOfItem(plan, selection.id) !== -1
    case 'hotel':
      return plan.hotel?.hotel.hotel_id === selection.id
    case 'ticket':
      return (plan.tickets ?? []).some((ticket) => ticket.ticket_id === selection.id)
  }
}

export const usePlanStore = defineStore('plan', () => {
  const plan = ref<TripPlan | null>(null)
  const selectedDayIndex = ref(0)
  const selectedItemId = ref<string | null>(null)
  const selection = ref<PlanSelection | null>(null)

  const hasPlan = computed(() => plan.value !== null)
  const days = computed<DayPlan[]>(() => plan.value?.days ?? [])
  const selectedDay = computed<DayPlan | null>(() => days.value[selectedDayIndex.value] ?? null)

  const placesById = computed(
    () => new Map<string, Place>((plan.value?.places ?? []).map((place) => [place.place_id, place])),
  )

  const sectionsByAgent = computed(
    () => new Map<AgentName, SectionState>((plan.value?.sections ?? []).map((s) => [s.agent, s])),
  )

  const selectedItem = computed<ItineraryItem | null>(
    () => selectedDay.value?.items?.find((item) => item.item_id === selectedItemId.value) ?? null,
  )

  /** 0-based position of the selected stop within its day, or -1. */
  const selectedItemIndex = computed(() =>
    selectedItem.value ? (selectedDay.value?.items ?? []).indexOf(selectedItem.value) : -1,
  )

  const selectedPlace = computed<Place | null>(() =>
    selectedItem.value ? placesById.value.get(selectedItem.value.place_id) ?? null : null,
  )

  /** Chip text for the selection, e.g. "Day 1 · Kiyomizu-dera" or "Ticket · Tokyo → Kyoto". */
  const selectionLabel = computed<string | null>(() => {
    const current = plan.value
    const sel = selection.value
    if (!current || !sel) return null
    switch (sel.kind) {
      case 'day': {
        const index = current.days.findIndex((day) => day.date === sel.id)
        return index === -1 ? null : `Day ${index + 1}`
      }
      case 'item': {
        const index = dayIndexOfItem(current, sel.id)
        const item = current.days[index]?.items?.find((i) => i.item_id === sel.id)
        return item ? `Day ${index + 1} · ${item.title}` : null
      }
      case 'hotel':
        return current.hotel ? `Hotel · ${current.hotel.hotel.name}` : null
      case 'ticket': {
        const ticket = (current.tickets ?? []).find((t) => t.ticket_id === sel.id)
        return ticket ? `Ticket · ${ticket.origin} → ${ticket.destination}` : null
      }
    }
  })

  function firstItemId(index: number): string | null {
    return days.value[index]?.items?.[0]?.item_id ?? null
  }

  /**
   * Replace the plan, keeping the selection where it still exists. A shorter trip moves the
   * selection to its last day; a stop the user deselected (map "close") stays deselected; a stop
   * moved to another day is followed there. A workspace selection whose part is gone is cleared.
   */
  function setPlan(next: TripPlan | null): void {
    const first = plan.value === null
    plan.value = next
    if (!next) {
      selectedDayIndex.value = 0
      selectedItemId.value = null
      selection.value = null
      return
    }
    if (selection.value && !selectionExists(next, selection.value)) selection.value = null
    const movedTo = selectedItemId.value === null ? -1 : dayIndexOfItem(next, selectedItemId.value)
    if (movedTo !== -1) selectedDayIndex.value = movedTo
    if (selectedDayIndex.value >= next.days.length) selectedDayIndex.value = Math.max(0, next.days.length - 1)
    const items = next.days[selectedDayIndex.value]?.items ?? []
    const kept = items.some((item) => item.item_id === selectedItemId.value)
    if (!kept && (first || selectedItemId.value !== null)) selectedItemId.value = items[0]?.item_id ?? null
  }

  function selectDay(index: number): void {
    if (index < 0 || index >= days.value.length) return
    selectedDayIndex.value = index
    selectedItemId.value = firstItemId(index)
  }

  function selectItem(itemId: string | null): void {
    if (itemId === null) {
      selectedItemId.value = null
      return
    }
    const dayIndex = days.value.findIndex((day) => day.items?.some((item) => item.item_id === itemId))
    if (dayIndex === -1) return
    selectedDayIndex.value = dayIndex
    selectedItemId.value = itemId
  }

  /**
   * Select one part of the plan to scope the next chat message. Selecting the part that is
   * already selected clears it; an id that is not in the plan is ignored.
   */
  function select(kind: FocusKind, id: string): void {
    const current = plan.value
    if (!current) return
    if (selection.value?.kind === kind && selection.value.id === id) {
      selection.value = null
      return
    }
    const next: PlanSelection = { kind, id }
    if (!selectionExists(current, next)) return
    selection.value = next
    if (kind === 'item') selectItem(id)
    else if (kind === 'day') selectDay(current.days.findIndex((day) => day.date === id))
  }

  function clearSelection(): void {
    selection.value = null
  }

  return {
    plan,
    selectedDayIndex,
    selectedItemId,
    selection,
    hasPlan,
    days,
    selectedDay,
    placesById,
    sectionsByAgent,
    selectedItem,
    selectedItemIndex,
    selectedPlace,
    selectionLabel,
    setPlan,
    selectDay,
    selectItem,
    select,
    clearSelection,
  }
})
