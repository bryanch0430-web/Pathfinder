<script setup lang="ts">
import { CircleHelp, Compass, Search, UserRound } from 'lucide-vue-next'
import { TABS, useUiStore, type Tab } from '@/stores/ui'

const ui = useUiStore()

/** Arrow keys move between tabs (WAI-ARIA tabs pattern, automatic activation). */
function onTabKeydown(event: KeyboardEvent, index: number): void {
  let next = -1
  if (event.key === 'ArrowRight') next = (index + 1) % TABS.length
  else if (event.key === 'ArrowLeft') next = (index - 1 + TABS.length) % TABS.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = TABS.length - 1
  if (next === -1) return
  event.preventDefault()
  const tab = TABS[next]!
  ui.setTab(tab.id)
  document.getElementById(tabId(tab.id))?.focus()
}

const tabId = (tab: Tab): string => `tab-${tab}`
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <span class="logo" aria-hidden="true"><Compass :size="18" /></span>
      <span class="name">Pathfinder</span>
    </div>

    <div class="tabs" role="tablist" aria-label="Views">
      <button
        v-for="(tab, index) in TABS"
        :id="tabId(tab.id)"
        :key="tab.id"
        type="button"
        role="tab"
        class="tab"
        :class="{ active: ui.activeTab === tab.id }"
        :aria-selected="ui.activeTab === tab.id"
        :aria-controls="`panel-${tab.id}`"
        :tabindex="ui.activeTab === tab.id ? 0 : -1"
        @click="ui.setTab(tab.id)"
        @keydown="onTabKeydown($event, index)"
      >
        {{ tab.label }}
      </button>
    </div>

    <div class="actions">
      <button type="button" class="icon-btn" aria-label="Search: ask Pathfinder" title="Ask Pathfinder" @click="ui.focusComposer()">
        <Search :size="18" aria-hidden="true" />
      </button>
      <button type="button" class="icon-btn" aria-label="Help" title="Help" @click="ui.openDialog('help')">
        <CircleHelp :size="18" aria-hidden="true" />
      </button>
      <span class="avatar" role="img" aria-label="You (no account: this session only)" title="This session only">
        <UserRound :size="18" aria-hidden="true" />
      </span>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-6);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.logo {
  display: inline-grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--primary);
  color: var(--accent);
}

.name {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  letter-spacing: -0.01em;
}

.tabs {
  display: inline-flex;
  gap: 2px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--card);
}

.tab {
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

.tab:hover:not(.active) {
  color: var(--text);
}

.tab.active {
  background: var(--primary);
  color: var(--primary-text);
}

.actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-2);
}

.avatar {
  display: inline-grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent-strong);
}

@media (max-width: 899px) {
  .topbar {
    grid-template-columns: 1fr auto;
    padding: var(--space-3) var(--space-4);
  }

  .tabs {
    grid-column: 1 / -1;
    grid-row: 2;
    justify-self: center;
  }
}

@media (max-width: 420px) {
  .tab {
    padding: 6px 12px;
  }
}
</style>
