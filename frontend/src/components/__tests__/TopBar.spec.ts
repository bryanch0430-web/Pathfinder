import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import TopBar from '../TopBar.vue'

describe('TopBar', () => {
  it('shows the Plan and Saved trips tabs, without Itinerary', () => {
    const wrapper = mount(TopBar, { global: { plugins: [createTestingPinia({ createSpy: vi.fn })] } })
    const tabs = wrapper.findAll('[role="tab"]').map((tab) => tab.text())
    expect(tabs).toEqual(['Plan', 'Saved trips'])
    expect(wrapper.find('#tab-itinerary').exists()).toBe(false)
  })
})
