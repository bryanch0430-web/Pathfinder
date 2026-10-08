import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import ChatPanel from '../ChatPanel.vue'
import { useSessionStore, type ChatEntry } from '@/stores/session'

let nextId = 1
function entry(partial: Partial<ChatEntry> & Pick<ChatEntry, 'role' | 'content'>): ChatEntry {
  return {
    id: nextId++,
    kind: 'message',
    route: null,
    at: null,
    missingFields: [],
    prompt: null,
    gateReason: null,
    answer: null,
    flagged: false,
    agentsRun: [],
    ...partial,
  }
}

function mountWith(log: ChatEntry[], extra: Record<string, unknown> = {}) {
  return mount(ChatPanel, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, initialState: { session: { log, ...extra } } })],
    },
  })
}

describe('ChatPanel routing status', () => {
  it('shows the route taken under each assistant message', () => {
    const wrapper = mountWith([
      entry({ role: 'user', content: 'Plan my trip' }),
      entry({ role: 'assistant', content: 'Here is your plan.', route: 'plan', agentsRun: ['attraction', 'hotel', 'weather', 'ticket'] }),
      entry({ role: 'user', content: 'Swap the hotel' }),
      entry({ role: 'assistant', content: 'Updated.', route: 'modify', agentsRun: ['hotel'] }),
      entry({ role: 'user', content: 'Weather?' }),
      entry({ role: 'assistant', content: 'Sunny.', route: 'ask' }),
      entry({ role: 'user', content: 'asdf' }),
      entry({ role: 'assistant', kind: 'clarification', content: 'Do you want a new plan?', route: 'unclear' }),
    ])

    const statuses = wrapper.findAll('[data-testid="route-status"]').map((node) => node.text())
    expect(statuses).toEqual(['Plan · 4 agents', 'Modify · hotel agent', 'Quick question', 'Needs clarification'])
  })

  it('never puts a status line under user messages', () => {
    const wrapper = mountWith([entry({ role: 'user', content: 'Hello' })])
    expect(wrapper.findAll('[data-testid="route-status"]')).toHaveLength(0)
  })

  it('shows the live route and agents of the turn in flight', () => {
    const wrapper = mountWith([entry({ role: 'user', content: 'Cheaper hotel please' })], {
      busy: true,
      currentRoute: 'modify',
      agents: {
        attraction: { phase: 'idle', status: null, note: null },
        hotel: { phase: 'running', status: null, note: null },
        weather: { phase: 'idle', status: null, note: null },
        ticket: { phase: 'idle', status: null, note: null },
      },
      liveReply: null,
    })
    expect(wrapper.get('[data-testid="live-status"]').text()).toBe('Modify · hotel agent')
    expect(wrapper.text()).toContain('Hotel…')
  })

  it('sends the typed message through the session store', async () => {
    const wrapper = mountWith([])
    const session = useSessionStore()
    await wrapper.get('textarea').setValue('Plan 5 days in Kyoto')
    await wrapper.get('form.composer').trigger('submit')
    expect(session.send).toHaveBeenCalledWith('Plan 5 days in Kyoto')
  })
})
