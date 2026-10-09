<script setup lang="ts">
import { computed } from 'vue'
import { usePlanStore } from '@/stores/plan'
import { LEFT_PANELS, useUiStore, type LeftPanel } from '@/stores/ui'

/**
 * Switch between the trip form and the chat in the Plan page's left column. Built like the top-bar
 * tabs (WAI-ARIA tabs pattern, automatic activation). A dot on "Chat" says a plan part is selected,
 * so the next chat message is about that part.
 */
const ui = useUiStore()
const planStore = usePlanStore()

const chatHasFocus = computed(() => planStore.selection !== null && ui.leftPanel !== 'chat')

const tabId = (panel: LeftPanel): string => `left-tab-${panel}`

function onKeydown(event: KeyboardEvent, index: number): void {
  let next = -1
  if (event.key === 'ArrowRight') next = (index + 1) % LEFT_PANELS.length
  else if (event.key === 'ArrowLeft') next = (index - 1 + LEFT_PANELS.length) % LEFT_PANELS.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = LEFT_PANELS.length - 1
  if (next === -1) return
  event.preventDefault()
  const panel = LEFT_PANELS[next]!
  ui.setLeftPanel(panel.id)
  document.getElementById(tabId(panel.id))?.focus()
}
</script>

<template>
  <div class="switch" role="tablist" aria-label="Plan with">
    <button
      v-for="(panel, index) in LEFT_PANELS"
      :id="tabId(panel.id)"
      :key="panel.id"
      type="button"
      role="tab"
      class="option"
      :class="{ active: ui.leftPanel === panel.id }"
      :aria-selected="ui.leftPanel === panel.id"
      :aria-controls="`left-panel-${panel.id}`"
      :tabindex="ui.leftPanel === panel.id ? 0 : -1"
      @click="ui.setLeftPanel(panel.id)"
      @keydown="onKeydown($event, index)"
    >
      {{ panel.label }}
      <span
        v-if="panel.id === 'chat' && chatHasFocus"
        class="dot"
        data-testid="chat-has-focus"
        :title="`About: ${planStore.selectionLabel ?? ''}`"
      >
        <span class="sr-only">(a plan part is selected)</span>
      </span>
    </button>
  </div>
</template>

<style scoped>
.switch {
  display: flex;
  gap: 2px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--card);
}

.option {
  display: inline-flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  min-height: 36px;
  padding: 6px 18px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-muted);
  font-weight: var(--weight-medium);
  cursor: pointer;
  white-space: nowrap;
}

.option:hover:not(.active) {
  color: var(--text);
}

.option.active {
  background: var(--primary);
  color: var(--primary-text);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-strong);
}
</style>
