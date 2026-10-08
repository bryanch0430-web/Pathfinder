import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import DaySelector from '../DaySelector.vue'
import DayTimeline from '../DayTimeline.vue'
import PlaceDetailCard from '../PlaceDetailCard.vue'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { makePlan } from '@/test/fixtures'

// The three cards share one plan store, as in the dashboard.
const Dashboard = defineComponent({
  render: () => h('div', [h(DaySelector), h(DayTimeline), h(PlaceDetailCard)]),
})

describe('DaySelector / DayTimeline sync', () => {
  beforeEach(() => {
    const pinia = createPinia()
    setActivePinia(pinia)
    usePlanStore().setPlan(makePlan())
  })

  const timelineTitles = (wrapper: ReturnType<typeof mount>) =>
    wrapper.findAll('[data-testid="timeline"] .name').map((node) => node.text().replace(/^Stop \d+: /, ''))

  it('starts on day 1 with its stops and the first stop selected', () => {
    const wrapper = mount(Dashboard)
    expect(wrapper.get('#day-title').text()).toBe('Day 1')
    expect(timelineTitles(wrapper)).toEqual(['Kiyomizu-dera · ¥500', 'Fushimi Inari Taisha · Free'])
    expect(wrapper.get('#place-title').text()).toBe('Kiyomizu-dera')
  })

  it('selecting a day updates the timeline and the place card', async () => {
    const wrapper = mount(Dashboard)
    const day2 = wrapper.get('button[aria-label^="Day 2"]')
    await day2.trigger('click')

    expect(day2.attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('button[aria-label^="Day 1"]').attributes('aria-pressed')).toBe('false')
    expect(wrapper.get('#day-title').text()).toBe('Day 2')
    expect(timelineTitles(wrapper)).toEqual(['Gion District · Free', 'Nijo Castle · ¥1,300'])
    expect(wrapper.get('#place-title').text()).toBe('Gion District')
    expect(usePlanStore().selectedItemId).toBe('gion@2')
  })

  it('arrow keys move between days', async () => {
    const wrapper = mount(Dashboard, { attachTo: document.body })
    await wrapper.get('button[aria-label^="Day 1"]').trigger('keydown', { key: 'ArrowRight' })
    expect(usePlanStore().selectedDayIndex).toBe(1)
    expect(wrapper.get('#day-title').text()).toBe('Day 2')
    wrapper.unmount()
  })

  it('clicking a stop in the timeline selects it for the place card', async () => {
    const wrapper = mount(Dashboard)
    const stops = wrapper.findAll('[data-testid="timeline"] .name')
    await stops[1]!.trigger('click')
    expect(usePlanStore().selectedItemId).toBe('inari@1')
    expect(wrapper.get('#place-title').text()).toBe('Fushimi Inari Taisha')
  })

  it('shows a lock on confirmed items', () => {
    const wrapper = mount(Dashboard)
    expect(wrapper.find('button[aria-label="Unconfirm Fushimi Inari Taisha"]').exists()).toBe(true)
    expect(wrapper.find('button[aria-label^="Confirm Kiyomizu-dera"]').exists()).toBe(true)
  })

  it('reordering with the keyboard sends the new order to the modify path', async () => {
    const session = useSessionStore()
    session.send = vi.fn().mockResolvedValue(undefined)
    usePlanStore().selectDay(1)
    const wrapper = mount(Dashboard)
    const handle = wrapper.get('button[aria-label^="Reorder Gion District"]')
    await handle.trigger('keydown', { key: ' ' })
    await handle.trigger('keydown', { key: 'ArrowDown' })
    expect(timelineTitles(wrapper)).toEqual(['Nijo Castle · ¥1,300', 'Gion District · Free'])
    await wrapper.get('button[aria-label^="Reorder Gion District"]').trigger('keydown', { key: ' ' })
    expect(session.send).toHaveBeenCalledWith('Change the order of the day 2 stops to: Nijo Castle, then Gion District')
  })
})
