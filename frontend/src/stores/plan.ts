/**
 * Plan store: the current TripPlan plus what the user has selected in it (day and stop).
 *
 * The session store writes the plan here after every turn; the dashboard cards only read it.
 * Selecting a day updates the map, the timeline and the place card together because all three
 * read `selectedDay` / `selectedItem` from this one store.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { AgentName, DayPlan, ItineraryItem, Place, SectionState, TripPlan } from '@/api'

export const usePlanStore = defineStore('plan', () => {
  const plan = ref<TripPlan | null>(null)
  const selectedDayIndex = ref(0)
  const selectedItemId = ref<string | null>(null)

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

  function firstItemId(index: number): string | null {
    return days.value[index]?.items?.[0]?.item_id ?? null
  }

  /**
   * Replace the plan, keeping the selection where it still exists. A shorter trip moves the
   * selection to its last day; a stop the user deselected (map "close") stays deselected.
   */
  function setPlan(next: TripPlan | null): void {
    const first = plan.value === null
    plan.value = next
    if (!next) {
      selectedDayIndex.value = 0
      selectedItemId.value = null
      return
    }
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

  return {
    plan,
    selectedDayIndex,
    selectedItemId,
    hasPlan,
    days,
    selectedDay,
    placesById,
    sectionsByAgent,
    selectedItem,
    selectedItemIndex,
    selectedPlace,
    setPlan,
    selectDay,
    selectItem,
  }
})
