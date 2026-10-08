import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import ClarificationCard from '../ClarificationCard.vue'
import { useSessionStore, type ChatEntry } from '@/stores/session'

function clarification(missingFields: string[], prompt = 'Plan 5 days in Kyoto'): ChatEntry {
  return {
    id: 1,
    role: 'assistant',
    kind: 'clarification',
    content: 'Could you tell me your dates, party size and budget?',
    route: 'unclear',
    at: null,
    missingFields,
    prompt,
    gateReason: missingFields.length > 0 ? 'missing_fields' : 'unclear_route',
    answer: null,
    flagged: false,
    agentsRun: [],
  }
}

function mountCard(entry: ChatEntry, interactive = true) {
  const wrapper = mount(ClarificationCard, {
    props: { entry, interactive },
    global: {
      plugins: [
        createTestingPinia({
          createSpy: vi.fn,
          initialState: { session: { context: { destination: 'Kyoto', origin: 'Tokyo' } } },
        }),
      ],
    },
  })
  const session = useSessionStore()
  vi.mocked(session.saveContext).mockResolvedValue(true)
  return { wrapper, session }
}

describe('ClarificationCard', () => {
  it('renders an input for each missing key variable only', () => {
    const { wrapper } = mountCard(clarification(['dates', 'party_size', 'budget']))
    const labels = wrapper.findAll('label').map((label) => label.text())
    expect(labels).toEqual(expect.arrayContaining(['Start date', 'End date', 'Party size']))
    expect(wrapper.find('input[aria-label="Budget amount"]').exists()).toBe(true)
    expect(labels).not.toContain('Destination')
  })

  it('saves the filled fields to the trip context, then resends the original message', async () => {
    const { wrapper, session } = mountCard(clarification(['dates', 'party_size', 'budget']))
    const inputs = wrapper.findAll('input')
    await inputs[0]!.setValue('2026-11-10')
    await inputs[1]!.setValue('2026-11-14')
    await wrapper.get('input[type="number"][min="1"]').setValue(2)
    await wrapper.get('input[aria-label="Budget amount"]').setValue(300000)
    await wrapper.get('button[type="submit"]').trigger('submit')
    await flushPromises()

    expect(session.saveContext).toHaveBeenCalledWith(
      expect.objectContaining({
        destination: 'Kyoto',
        origin: 'Tokyo',
        start_date: '2026-11-10',
        end_date: '2026-11-14',
        party_size: 2,
        budget: { amount: 300000, currency: 'JPY' },
      }),
    )
    expect(session.send).toHaveBeenCalledWith('Plan 5 days in Kyoto')
  })

  it('does not continue while a missing field is still empty', async () => {
    const { wrapper, session } = mountCard(clarification(['party_size']))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(session.saveContext).not.toHaveBeenCalled()
    expect(session.send).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Enter a whole number from 1 to 50.')
  })

  it('sends a free-text answer when the route was unclear without missing fields', async () => {
    const { wrapper, session } = mountCard(clarification([]))
    await wrapper.get('input').setValue('A new trip plan')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(session.saveContext).not.toHaveBeenCalled()
    expect(session.send).toHaveBeenCalledWith('A new trip plan')
  })

  it('is read-only when it is not the latest clarification', () => {
    const { wrapper } = mountCard(clarification(['dates']), false)
    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.get('[data-testid="route-status"]').text()).toBe('Needs clarification')
  })
})
