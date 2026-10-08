/** UI store: the active top-bar tab and which dialog (if any) is open. No backend data here. */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type Tab = 'plan' | 'itinerary' | 'saved'
export type Dialog = 'rating' | 'trip-details' | 'help'

export const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: 'plan', label: 'Plan' },
  { id: 'itinerary', label: 'Itinerary' },
  { id: 'saved', label: 'Saved trips' },
]

export const useUiStore = defineStore('ui', () => {
  const activeTab = ref<Tab>('plan')
  const dialog = ref<Dialog | null>(null)
  /** Bumped to ask the chat composer to take focus (top-bar search button). */
  const composerFocusRequest = ref(0)

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

  return { activeTab, dialog, composerFocusRequest, setTab, openDialog, closeDialog, focusComposer }
})
