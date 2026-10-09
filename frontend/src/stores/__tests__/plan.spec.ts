import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePlanStore } from '../plan'
import { makeHotelStay, makePlan, makeTicket } from '@/test/fixtures'

describe('plan store selection', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('selects the first stop of day 1 for a new plan', () => {
    const store = usePlanStore()
    store.setPlan(makePlan())
    expect([store.selectedDayIndex, store.selectedItemId]).toEqual([0, 'kiyomizu@1'])
  })

  it('moves to the last day when the trip gets shorter', () => {
    const store = usePlanStore()
    const plan = makePlan()
    store.setPlan(plan)
    store.selectDay(1)
    store.setPlan({ ...plan, version: 2, days: plan.days.slice(0, 1) })
    expect(store.selectedDayIndex).toBe(0)
    expect(store.selectedItemId).toBe('kiyomizu@1')
  })

  it('keeps a cleared selection cleared across plan updates', () => {
    const store = usePlanStore()
    const plan = makePlan()
    store.setPlan(plan)
    store.selectItem(null)
    store.setPlan({ ...plan, version: 2 })
    expect(store.selectedItemId).toBeNull()
  })
})

describe('plan store workspace selection', () => {
  beforeEach(() => setActivePinia(createPinia()))

  const fullPlan = () => makePlan({ hotel: makeHotelStay(), tickets: [makeTicket()] })

  it('selects a stop together with its day and the map stop', () => {
    const store = usePlanStore()
    store.setPlan(fullPlan())
    store.select('item', 'nijo@2')
    expect(store.selection).toEqual({ kind: 'item', id: 'nijo@2' })
    expect([store.selectedDayIndex, store.selectedItemId]).toEqual([1, 'nijo@2'])
    expect(store.selectionLabel).toBe('Day 2 · Nijo Castle')
  })

  it('selecting the same part again clears it', () => {
    const store = usePlanStore()
    store.setPlan(fullPlan())
    store.select('item', 'kiyomizu@1')
    store.select('item', 'kiyomizu@1')
    expect(store.selection).toBeNull()
    expect(store.selectionLabel).toBeNull()
  })

  it('selects a day by date and moves the day view', () => {
    const store = usePlanStore()
    store.setPlan(fullPlan())
    store.select('day', '2026-11-11')
    expect(store.selection).toEqual({ kind: 'day', id: '2026-11-11' })
    expect(store.selectedDayIndex).toBe(1)
    expect(store.selectionLabel).toBe('Day 2')
  })

  it('labels the hotel and a ticket from their own fields', () => {
    const store = usePlanStore()
    store.setPlan(fullPlan())
    store.select('hotel', 'piece-sanjo')
    expect(store.selectionLabel).toBe('Hotel · Piece Hostel Sanjo')
    store.select('ticket', 'nozomi-1')
    expect(store.selection).toEqual({ kind: 'ticket', id: 'nozomi-1' })
    expect(store.selectionLabel).toBe('Ticket · Tokyo → Kyoto')
  })

  it('ignores a part that is not in the plan', () => {
    const store = usePlanStore()
    store.setPlan(fullPlan())
    store.select('item', 'missing@9')
    store.select('hotel', 'other-hotel')
    expect(store.selection).toBeNull()
  })

  it('clearSelection clears only the chat scope', () => {
    const store = usePlanStore()
    store.setPlan(fullPlan())
    store.select('item', 'gion@2')
    store.clearSelection()
    expect(store.selection).toBeNull()
    expect(store.selectedItemId).toBe('gion@2')
  })

  it('clears the selection when a plan update removes the part', () => {
    const store = usePlanStore()
    const plan = fullPlan()
    store.setPlan(plan)
    store.select('ticket', 'nozomi-1')
    store.setPlan({ ...plan, version: 2 })
    expect(store.selection).toEqual({ kind: 'ticket', id: 'nozomi-1' })
    store.setPlan({ ...plan, version: 3, tickets: [] })
    expect(store.selection).toBeNull()

    store.select('day', '2026-11-11')
    store.setPlan({ ...plan, version: 4, days: plan.days.slice(0, 1) })
    expect(store.selection).toBeNull()

    store.select('hotel', 'piece-sanjo')
    store.setPlan(null)
    expect(store.selection).toBeNull()
  })

  it('follows a selected stop that moved to another day', () => {
    const store = usePlanStore()
    const plan = fullPlan()
    store.setPlan(plan)
    store.select('item', 'kiyomizu@1')
    const [day1, day2] = plan.days
    const moved = day1!.items!.find((item) => item.item_id === 'kiyomizu@1')!
    store.setPlan({
      ...plan,
      version: 2,
      days: [
        { ...day1!, items: day1!.items!.filter((item) => item !== moved) },
        { ...day2!, items: [...day2!.items!, moved] },
      ],
    })
    expect(store.selection).toEqual({ kind: 'item', id: 'kiyomizu@1' })
    expect([store.selectedDayIndex, store.selectedItemId]).toEqual([1, 'kiyomizu@1'])
    expect(store.selectionLabel).toBe('Day 2 · Kiyomizu-dera')
  })
})
