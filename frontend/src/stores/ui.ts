/**
 * UI store: the active top-bar tab, which dialog (if any) is open and whether the trip input
 * panel is expanded. No backend data here.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type Tab = 'plan' | 'saved'
export type Dialog = 'rating' | 'trip-details' | 'help'

export const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: 'plan', label: 'Plan' },
  { id: 'saved', label: 'Saved trips' },
]

export const useUiStore = defineStore('ui', () => {
  const activeTab = ref<Tab>('plan')
  const dialog = ref<Dialog | null>(null)
  /** Bumped to ask the chat composer to take focus (top-bar search button). */
  const composerFocusRequest = ref(0)
  /** The trip input panel on the Plan page: expanded until a plan arrives, then a one-line summary. */
  const inputPanelExpanded = ref(true)

  function setTab(tab: Tab): void {
    activeTab.value = tab
  }

  function openDialog(name: Dialog): void {
    dialog.value = name
  }

  function closeDialog(): void {
    dialog.value = null
  }

  function focusComposer(): void {
    activeTab.value = 'plan'
    composerFocusRequest.value += 1
  }

  /** Open the trip input panel to edit the trip context (replaces the "Trip details" dialog). */
  function expandInputPanel(): void {
    activeTab.value = 'plan'
    inputPanelExpanded.value = true
  }

  function collapseInputPanel(): void {
    inputPanelExpanded.value = false
  }

  return {
    activeTab,
    dialog,
    composerFocusRequest,
    inputPanelExpanded,
    setTab,
    openDialog,
    closeDialog,
    focusComposer,
    expandInputPanel,
    collapseInputPanel,
  }
})
