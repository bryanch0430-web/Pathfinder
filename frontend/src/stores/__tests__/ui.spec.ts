import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { LEFT_PANELS, TABS, useUiStore } from '../ui'

describe('ui store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('offers only the Plan and Saved trips tabs', () => {
    expect(TABS.map((tab) => tab.label)).toEqual(['Plan', 'Saved trips'])
  })

  it('starts with the trip input panel expanded and toggles it', () => {
    const ui = useUiStore()
    expect(ui.inputPanelExpanded).toBe(true)
    ui.collapseInputPanel()
    expect(ui.inputPanelExpanded).toBe(false)
    ui.setTab('saved')
    ui.expandInputPanel()
    expect([ui.inputPanelExpanded, ui.activeTab]).toEqual([true, 'plan'])
  })

  it('starts the left column on the trip form and lets the user switch to the chat', () => {
    const ui = useUiStore()
    expect(LEFT_PANELS.map((panel) => panel.label)).toEqual(['Your trip', 'Chat'])
    expect(ui.leftPanel).toBe('trip')
    ui.setLeftPanel('chat')
    expect(ui.leftPanel).toBe('chat')
  })

  it('switching back to the trip form expands it; the search button and Edit pick their panel', () => {
    const ui = useUiStore()
    ui.collapseInputPanel()
    ui.setLeftPanel('chat')
    ui.setLeftPanel('trip')
    expect(ui.inputPanelExpanded).toBe(true)

    ui.focusComposer()
    expect(ui.leftPanel).toBe('chat')
    ui.expandInputPanel()
    expect(ui.leftPanel).toBe('trip')
  })
})
