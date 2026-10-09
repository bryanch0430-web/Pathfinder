import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import PlanWorkspace from '../PlanWorkspace.vue'
import PlaceDetailCard from '../PlaceDetailCard.vue'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { makeHotelStay, makePlan, makeTicket } from '@/test/fixtures'

// The workspace and the place card share one plan store, as in the dashboard.
const Dashboard = defineComponent({
  render: () => h('div', [h(PlanWorkspace), h(PlaceDetailCard)]),
})

const stopButton = (wrapper: ReturnType<typeof mount>, title: string) =>
  wrapper.findAll('[data-testid="stop"] > [role="button"]').find((node) => node.text().includes(title))!

describe('PlanWorkspace', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows the empty state without a plan', () => {
    const wrapper = mount(PlanWorkspace)
    expect(wrapper.get('.empty').text()).toBe('Fill in your trip on the left and press Generate.')
  })

  describe('with a plan', () => {
    beforeEach(() => {
      usePlanStore().setPlan(makePlan({ hotel: makeHotelStay(), tickets: [makeTicket()] }))
    })

    it('renders one section per day with its stops in time order', () => {
      const plan = makePlan()
      // Out of time order on purpose: the day section sorts by start time.
      plan.days[1]!.items = [...plan.days[1]!.items!].reverse()
      usePlanStore().setPlan(plan)
      const wrapper = mount(Dashboard)
      const days = wrapper.findAll('[data-testid="day"]')
      expect(days.map((day) => day.get('h3').text())).toEqual(['Day 1 · Tue, 10 Nov 2026', 'Day 2 · Wed, 11 Nov 2026'])
      const titles = days[1]!.findAll('.title').map((node) => node.text())
      expect(titles).toEqual(['Gion District', 'Nijo Castle'])
      expect(days[0]!.text()).toContain('Partly cloudy')
      expect(wrapper.get('#place-title').text()).toBe('Kiyomizu-dera')
    })

    it('the day-jump strip points the map and place card at that day without scoping the chat', async () => {
      const wrapper = mount(Dashboard)
      await wrapper.get('nav button[aria-label^="Day 2"]').trigger('click')
      const planStore = usePlanStore()
      expect(planStore.selectedDayIndex).toBe(1)
      expect(planStore.selection).toBeNull()
      expect(wrapper.get('#place-title').text()).toBe('Gion District')
    })

    it('clicking a stop selects it, and clicking it again clears the selection', async () => {
      const wrapper = mount(Dashboard)
      const planStore = usePlanStore()
      await stopButton(wrapper, 'Fushimi Inari').trigger('click')
      expect(planStore.selection).toEqual({ kind: 'item', id: 'inari@1' })
      expect(stopButton(wrapper, 'Fushimi Inari').attributes('aria-pressed')).toBe('true')
      expect(wrapper.get('[data-testid="stop"].selected').text()).toContain('Fushimi Inari')
      expect(wrapper.get('#place-title').text()).toBe('Fushimi Inari Taisha')

      await stopButton(wrapper, 'Fushimi Inari').trigger('click')
      expect(planStore.selection).toBeNull()
      expect(stopButton(wrapper, 'Fushimi Inari').attributes('aria-pressed')).toBe('false')
    })

    it('selects a day, the hotel and a ticket', async () => {
      const wrapper = mount(PlanWorkspace)
      const planStore = usePlanStore()
      await wrapper.findAll('[data-testid="day"] h3 [role="button"]')[1]!.trigger('click')
      expect(planStore.selectionLabel).toBe('Day 2')
      await wrapper.get('[data-testid="hotel"] > [role="button"]').trigger('click')
      expect(planStore.selection).toEqual({ kind: 'hotel', id: 'piece-sanjo' })
      await wrapper.get('[data-testid="ticket"] > [role="button"]').trigger('click')
      expect(planStore.selectionLabel).toBe('Ticket · Tokyo → Kyoto')
      expect(wrapper.get('[data-testid="hotel"] > [role="button"]').attributes('aria-pressed')).toBe('false')
    })

    it('Enter and Space select from the keyboard, and Escape clears', async () => {
      const wrapper = mount(PlanWorkspace)
      const planStore = usePlanStore()
      await stopButton(wrapper, 'Nijo Castle').trigger('keydown', { key: 'Enter' })
      expect(planStore.selection).toEqual({ kind: 'item', id: 'nijo@2' })
      await wrapper.get('[data-testid="hotel"] > [role="button"]').trigger('keydown', { key: ' ' })
      expect(planStore.selection).toEqual({ kind: 'hotel', id: 'piece-sanjo' })
      await wrapper.get('[data-testid="hotel"] > [role="button"]').trigger('keydown', { key: 'Escape' })
      expect(planStore.selection).toBeNull()
    })

    it('shows lock state, and the lock control only on the selected part', async () => {
      const wrapper = mount(PlanWorkspace)
      expect(stopButton(wrapper, 'Fushimi Inari').text()).toContain('Locked')
      expect(wrapper.find('button[aria-label^="Unlock Fushimi"]').exists()).toBe(false)
      await stopButton(wrapper, 'Fushimi Inari').trigger('click')
      expect(wrapper.find('button[aria-label="Unlock Fushimi Inari Taisha"]').exists()).toBe(true)
      expect(wrapper.get('[data-testid="stop-editor"] input[type="time"]').attributes('disabled')).toBeDefined()
    })
  })

  describe('manual edits on the selected stop', () => {
    beforeEach(() => {
      usePlanStore().setPlan(makePlan({ hotel: makeHotelStay() }))
      usePlanStore().select('item', 'kiyomizu@1')
    })

    it('saves a changed time through the store and shows a refusal inline', async () => {
      const session = useSessionStore()
      session.editItem = vi
        .fn()
        .mockResolvedValue({ ok: false, error: 'end_time 12:00 must be after start_time 12:30' })
      const wrapper = mount(PlanWorkspace)
      const editor = wrapper.get('[data-testid="stop-editor"]')
      await editor.findAll('input[type="time"]')[0]!.setValue('12:30')
      await editor.get('form.times').trigger('submit')
      await flushPromises()

      expect(session.editItem).toHaveBeenCalledWith('kiyomizu@1', { start_time: '12:30' })
      expect(editor.get('form.times [role="alert"]').text()).toBe('end_time 12:00 must be after start_time 12:30')
    })

    it('edits the note, moves the stop to another day and deletes it', async () => {
      const session = useSessionStore()
      session.editItem = vi.fn().mockResolvedValue({ ok: true })
      session.deleteItem = vi.fn().mockResolvedValue({ ok: true })
      const wrapper = mount(PlanWorkspace)
      const editor = wrapper.get('[data-testid="stop-editor"]')

      await editor.get('textarea').setValue('Go early')
      await editor.findAll('form')[1]!.trigger('submit')
      expect(session.editItem).toHaveBeenLastCalledWith('kiyomizu@1', { note: 'Go early' })

      await editor.get('select').setValue('2026-11-11')
      await editor.get('form.move').trigger('submit')
      expect(session.editItem).toHaveBeenLastCalledWith('kiyomizu@1', { day: '2026-11-11' })

      const del = editor.findAll('button').find((node) => node.text().includes('Delete stop'))!
      await del.trigger('click')
      expect(session.deleteItem).toHaveBeenCalledWith('kiyomizu@1')
    })

    it('shows a failed lock inline instead of in the banner', async () => {
      const session = useSessionStore()
      session.setItemConfirmed = vi.fn().mockImplementation(async () => {
        session.lastError = 'Kiyomizu-dera is no longer in the plan'
        return false
      })
      const wrapper = mount(PlanWorkspace)
      await wrapper.get('button[aria-label^="Lock Kiyomizu-dera"]').trigger('click')
      await flushPromises()

      expect(session.setItemConfirmed).toHaveBeenCalledWith('kiyomizu@1', true)
      expect(wrapper.get('[data-testid="stop-editor"] .actions [role="alert"]').text()).toBe(
        'Kiyomizu-dera is no longer in the plan',
      )
      expect(session.lastError).toBeNull()
    })
  })
})
