import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePlanStore } from '../plan'
import { makePlan } from '@/test/fixtures'

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
