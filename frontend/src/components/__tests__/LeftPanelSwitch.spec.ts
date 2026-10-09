import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import LeftPanelSwitch from '../LeftPanelSwitch.vue'
import { usePlanStore } from '@/stores/plan'
import { useUiStore } from '@/stores/ui'
import { makePlan } from '@/test/fixtures'

describe('LeftPanelSwitch', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('shows "Your trip" and "Chat" as tabs with the trip form selected first', () => {
    const wrapper = mount(LeftPanelSwitch)
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.map((tab) => tab.text())).toEqual(['Your trip', 'Chat'])
    expect(tabs.map((tab) => tab.attributes('aria-selected'))).toEqual(['true', 'false'])
    expect(tabs.map((tab) => tab.attributes('aria-controls'))).toEqual(['left-panel-trip', 'left-panel-chat'])
  })

  it('switches the left panel on click and with the arrow, Home and End keys', async () => {
    const wrapper = mount(LeftPanelSwitch, { attachTo: document.body })
    const ui = useUiStore()

    await wrapper.findAll('[role="tab"]')[1]!.trigger('click')
    expect(ui.leftPanel).toBe('chat')
    await wrapper.findAll('[role="tab"]')[1]!.trigger('keydown', { key: 'ArrowRight' })
    expect(ui.leftPanel).toBe('trip')
    await wrapper.findAll('[role="tab"]')[0]!.trigger('keydown', { key: 'End' })
    expect(ui.leftPanel).toBe('chat')
    expect(document.activeElement?.id).toBe('left-tab-chat')
    await wrapper.findAll('[role="tab"]')[1]!.trigger('keydown', { key: 'Home' })
    expect(ui.leftPanel).toBe('trip')
    wrapper.unmount()
  })

  it('marks the Chat tab while a plan part is selected and the chat is hidden', async () => {
    const planStore = usePlanStore()
    planStore.setPlan(makePlan())
    const wrapper = mount(LeftPanelSwitch)
    expect(wrapper.find('[data-testid="chat-has-focus"]').exists()).toBe(false)

    planStore.select('day', planStore.plan!.days[0]!.date)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="chat-has-focus"]').exists()).toBe(true)

    useUiStore().setLeftPanel('chat')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="chat-has-focus"]').exists()).toBe(false)
  })
})
