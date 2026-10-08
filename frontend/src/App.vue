<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import TripForm from '@/components/TripForm.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import ItineraryView from '@/components/ItineraryView.vue'
import MapView from '@/components/MapView.vue'
import RatingControl from '@/components/RatingControl.vue'

const store = useSessionStore()

const backendText = computed(() => {
  const state = store.backend
  if (state.online === null) return 'Checking backend...'
  if (!state.online) return 'Backend unreachable'
  const { storage_backend, router_provider, agent_llm_provider } = state.info
  return `Backend online (storage: ${storage_backend}, router: ${router_provider}, agents: ${agent_llm_provider})`
})

const backendClass = computed(() => {
  const state = store.backend
  if (state.online === null) return ''
  return state.online ? 'badge-ok' : 'badge-danger'
})

function connect(): void {
  void store.checkHealth()
  void store.ensureSession().catch(() => {
    // The failure is already shown in the error banner.
  })
}

onMounted(connect)
onBeforeUnmount(() => store.closeStream())
</script>

<template>
  <a class="skip-link" href="#main">Skip to main content</a>

  <header class="app-header">
    <div class="brand">
      <h1>Pathfinder</h1>
      <p class="muted small">State-aware trip planner</p>
    </div>
    <div class="header-actions">
      <span class="badge" :class="backendClass" role="status">{{ backendText }}</span>
      <button type="button" class="btn btn-small" :disabled="store.busy || store.loading" @click="store.newSession()">
        New session
      </button>
    </div>
  </header>

  <div v-if="store.lastError" class="banner" role="alert">
    <p>{{ store.lastError }}</p>
    <div class="row">
      <button v-if="store.sessionLost" type="button" class="btn btn-small btn-primary" @click="store.newSession()">
        Start a new session
      </button>
      <button v-else-if="!store.sessionId && !store.loading" type="button" class="btn btn-small" @click="connect">
        Retry
      </button>
      <button type="button" class="btn btn-small" @click="store.dismissError()">Dismiss</button>
    </div>
  </div>

  <main id="main" class="layout">
    <div class="column">
      <TripForm />
      <ChatPanel />
    </div>
    <div class="column">
      <ItineraryView />
      <MapView />
      <RatingControl />
    </div>
  </main>
</template>

<style scoped>
.skip-link {
  position: absolute;
  left: 8px;
  top: -48px;
  z-index: 10;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--primary);
  color: var(--primary-text);
}

.skip-link:focus {
  top: 8px;
}

.app-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  max-width: 1400px;
  margin: 0 auto;
  padding: 14px 20px;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

h1 {
  font-size: 1.4rem;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  max-width: 1360px;
  margin: 0 auto 8px;
  padding: 10px 14px;
  border: 1px solid var(--danger);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--text);
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  align-items: start;
  gap: 16px;
  max-width: 1400px;
  margin: 0 auto;
  padding: 8px 20px 32px;
}

.column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
    padding: 8px 12px 24px;
  }

  .app-header {
    padding: 12px;
  }

  .banner {
    margin: 0 12px 8px;
  }
}
</style>
