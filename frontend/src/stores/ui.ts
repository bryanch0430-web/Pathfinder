/**
 * UI store: the active top-bar tab, which dialog (if any) is open, which left-column panel (trip
 * form or chat) the user chose and whether the trip input panel is expanded. No backend data here.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type Tab = 'plan' | 'saved'
export type Dialog = 'rating' | 'help'
export type LeftPanel = 'trip' | 'chat'

export const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: 'plan', label: 'Plan' },
  { id: 'saved', label: 'Saved trips' },
]

/** The Plan page's left column shows one of these at a time; the user switches between them. */
export const LEFT_PANELS: ReadonlyArray<{ id: LeftPanel; label: string }> = [
  { id: 'trip', label: 'Your trip' },
  { id: 'chat', label: 'Chat' },
]

export const useUiStore = defineStore('ui', () => {
  const activeTab = ref<Tab>('plan')
  const dialog = ref<Dialog | null>(null)
  /** Bumped to ask the chat composer to take focus (top-bar search button). */
  const composerFocusRequest = ref(0)
  /** The trip input panel on the Plan page: expanded until a plan arrives, then a one-line summary. */
  const inputPanelExpanded = ref(true)
  const leftPanel = ref<LeftPanel>('trip')

  function setTab(tab: Tab): void {
    activeTab.value = tab
  }

  function openDialog(name: Dialog): void {
    dialog.value = name
  }

  function closeDialog(): void {
    dialog.value = null
  }

  /** Choosing the trip form shows it in full, so the user sees every field they came to edit. */
  function setLeftPanel(panel: LeftPanel): void {
    leftPanel.value = panel
    if (panel === 'trip') inputPanelExpanded.value = true
  }

  function focusComposer(): void {
    activeTab.value = 'plan'
    leftPanel.value = 'chat'
    composerFocusRequest.value += 1
  }

  /** Open the trip input panel to edit the trip context (replaces the "Trip details" dialog). */
  function expandInputPanel(): void {
    activeTab.value = 'plan'
    setLeftPanel('trip')
  }

  function collapseInputPanel(): void {
    inputPanelExpanded.value = false
  }

  return {
    activeTab,
    dialog,
    composerFocusRequest,
    inputPanelExpanded,
    leftPanel,
    setTab,
    openDialog,
    closeDialog,
    focusComposer,
    expandInputPanel,
    collapseInputPanel,
    setLeftPanel,
  }
})
