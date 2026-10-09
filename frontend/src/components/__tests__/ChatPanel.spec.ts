import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import ChatPanel from '../ChatPanel.vue'
import { useSessionStore, type ChatEntry } from '@/stores/session'
import { usePlanStore } from '@/stores/plan'
import { makePlan } from '@/test/fixtures'

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

function mountWith(log: ChatEntry[], extra: Record<string, unknown> = {}, plan: Record<string, unknown> = {}) {
  return mount(ChatPanel, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, initialState: { session: { log, ...extra }, plan } })],
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

describe('ChatPanel focus chip', () => {
  it('shows what the next message is about, and the ✕ clears the selection', async () => {
    const wrapper = mountWith([], {}, { plan: makePlan(), selection: { kind: 'item', id: 'kiyomizu@1' } })
    const chip = wrapper.get('[data-testid="focus-chip"]')
    expect(chip.text()).toBe('About: Day 1 · Kiyomizu-dera')
    await chip.get('button').trigger('click')
    expect(usePlanStore().clearSelection).toHaveBeenCalled()
  })

  it('has no chip without a selection', () => {
    const wrapper = mountWith([], {}, { plan: makePlan(), selection: null })
    expect(wrapper.find('[data-testid="focus-chip"]').exists()).toBe(false)
  })

  it('puts the typed text back when the server refuses the focused message', async () => {
    const wrapper = mountWith([], {}, { plan: makePlan(), selection: { kind: 'day', id: '2026-11-11' } })
    vi.mocked(useSessionStore().send).mockResolvedValue({ ok: false, draft: 'Something cheaper' })
    const textarea = wrapper.get('textarea')
    await textarea.setValue('Something cheaper')
    await wrapper.get('form.composer').trigger('submit')
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('Something cheaper')
  })
})
