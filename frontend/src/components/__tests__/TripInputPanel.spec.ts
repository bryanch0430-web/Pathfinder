import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TripInputPanel from '../TripInputPanel.vue'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import { makePlan } from '@/test/fixtures'

describe('TripInputPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    const session = useSessionStore()
    session.context = {
      destination: 'Kyoto',
      start_date: '2026-11-10',
      end_date: '2026-11-12',
      party_size: 2,
      budget: { amount: 1500, currency: 'USD' },
    }
    session.saveContext = vi.fn().mockResolvedValue(true)
    session.send = vi.fn().mockResolvedValue({ ok: true, draft: null })
  })

  it('is expanded with the trip form while there is no plan', () => {
    const wrapper = mount(TripInputPanel)
    expect(wrapper.find('[data-testid="generate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="trip-summary"]').exists()).toBe(false)
  })

  it('collapses to a one-line summary once a plan arrives, and Edit expands it again', async () => {
    const wrapper = mount(TripInputPanel)
    usePlanStore().setPlan(makePlan())
    await wrapper.vm.$nextTick()

    const summary = wrapper.get('[data-testid="trip-summary"]').text()
    expect(summary).toContain('Kyoto · 10–12 Nov · 2 people · ')
    expect(summary).toMatch(/1,500/)
    expect(wrapper.find('[data-testid="generate"]').exists()).toBe(false)

    await wrapper.get('button[aria-expanded="false"]').trigger('click')
    expect(useUiStore().inputPanelExpanded).toBe(true)
    expect(wrapper.find('[data-testid="generate"]').exists()).toBe(true)
  })

  it('Generate saves the context, then sends "Plan my trip", then collapses', async () => {
    usePlanStore().setPlan(makePlan())
    const session = useSessionStore()
    const wrapper = mount(TripInputPanel)
    await wrapper.get('button[aria-expanded="false"]').trigger('click')

    await wrapper.get('[data-testid="generate"]').trigger('click')
    await flushPromises()

    expect(session.saveContext).toHaveBeenCalledWith(expect.objectContaining({ destination: 'Kyoto', party_size: 2 }))
    expect(session.send).toHaveBeenCalledWith('Plan my trip')
    const saveOrder = vi.mocked(session.saveContext).mock.invocationCallOrder[0]!
    expect(saveOrder).toBeLessThan(vi.mocked(session.send).mock.invocationCallOrder[0]!)
    expect(wrapper.find('[data-testid="trip-summary"]').exists()).toBe(true)
  })

  it('does not send when saving the context fails', async () => {
    const session = useSessionStore()
    session.saveContext = vi.fn().mockResolvedValue(false)
    const wrapper = mount(TripInputPanel)
    await wrapper.get('[data-testid="generate"]').trigger('click')
    await flushPromises()
    expect(session.send).not.toHaveBeenCalled()
  })

  it('disables Generate while a turn is running', async () => {
    const wrapper = mount(TripInputPanel)
    useSessionStore().busy = true
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-testid="generate"]').attributes('disabled')).toBeDefined()
  })
})
