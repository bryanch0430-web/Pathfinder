import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { TABS, useUiStore } from '../ui'

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
})
