import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import StalenessBadge from '../StalenessBadge.vue'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { makePlan } from '@/test/fixtures'
import type { SectionState } from '@/api'

function mountBadge(weather: SectionState, props: Record<string, unknown> = {}) {
  const pinia = createTestingPinia({ createSpy: vi.fn, stubActions: false })
  const plan = makePlan({
    sections: [{ agent: 'attraction', status: 'ok', fetched_at: '2026-10-08T18:00:00Z' }, weather],
  })
  usePlanStore(pinia).setPlan(plan)
  const session = useSessionStore(pinia)
  session.send = vi.fn().mockResolvedValue(undefined)
  const wrapper = mount(StalenessBadge, { props: { agent: 'weather', ...props }, global: { plugins: [pinia] } })
  return { wrapper, session }
}

describe('StalenessBadge', () => {
  it('shows only a timestamp caption when the section is fresh', () => {
    const { wrapper } = mountBadge({ agent: 'weather', status: 'ok', fetched_at: '2026-10-08T18:00:00Z' })
    expect(wrapper.text()).toContain('Updated')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('refreshes a stale section through the modify path (that section only)', async () => {
    const { wrapper, session } = mountBadge({ agent: 'weather', status: 'stale', fetched_at: '2026-10-01T00:00:00Z' })
    const button = wrapper.get('button')
    expect(button.text()).toContain('stale – refresh')
    await button.trigger('click')
    expect(session.send).toHaveBeenCalledTimes(1)
    expect(session.send).toHaveBeenCalledWith('re-check the weather')
  })

  it('offers "unavailable – refresh" when the section is unavailable', async () => {
    const { wrapper, session } = mountBadge({ agent: 'weather', status: 'unavailable', reason: 'timeout' })
    const button = wrapper.get('button')
    expect(button.text()).toContain('unavailable – refresh')
    expect(wrapper.text()).not.toContain('Updated')
    await button.trigger('click')
    expect(session.send).toHaveBeenCalledWith('re-check the weather')
  })

  it('treats a missing fact as unavailable even if the section is OK', () => {
    const { wrapper } = mountBadge({ agent: 'weather', status: 'ok', fetched_at: '2026-10-08T18:00:00Z' }, { missing: true })
    expect(wrapper.get('button').text()).toContain('unavailable – refresh')
  })
})
