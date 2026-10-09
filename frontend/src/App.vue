<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import TopBar from '@/components/TopBar.vue'
import TripInputPanel from '@/components/TripInputPanel.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import PlanWorkspace from '@/components/PlanWorkspace.vue'
import MapView from '@/components/MapView.vue'
import PlaceDetailCard from '@/components/PlaceDetailCard.vue'
import BudgetCard from '@/components/BudgetCard.vue'
import SavedTripsList from '@/components/SavedTripsList.vue'
import RatingDialog from '@/components/RatingDialog.vue'
import AppDialog from '@/components/AppDialog.vue'

const session = useSessionStore()
const ui = useUiStore()

function connect(): void {
  void session.checkHealth()
  void session.ensureSession().catch(() => {
    // The failure is already shown in the error banner.
  })
}

onMounted(connect)
onBeforeUnmount(() => session.closeStream())
</script>

<template>
  <a class="skip-link" href="#main">Skip to main content</a>

  <TopBar />

  <div v-if="session.lastError" class="banner" role="alert">
    <p>{{ session.lastError }}</p>
    <div class="row">
      <button v-if="session.sessionLost" type="button" class="btn btn-small btn-primary" @click="session.newSession()">
        Start a new session
      </button>
      <button v-else-if="!session.sessionId && !session.loading" type="button" class="btn btn-small" @click="connect">
        Retry
      </button>
      <button type="button" class="btn btn-small" @click="session.dismissError()">Dismiss</button>
    </div>
  </div>

  <main id="main">
    <div
      v-show="ui.activeTab === 'plan'"
      id="panel-plan"
      class="dashboard"
      role="tabpanel"
      aria-labelledby="tab-plan"
    >
      <div class="col col-left">
        <TripInputPanel />
        <ChatPanel />
      </div>
      <div class="col col-center">
        <PlanWorkspace />
      </div>
      <div class="col col-right">
        <MapView />
        <PlaceDetailCard />
        <BudgetCard />
      </div>
    </div>

    <div v-if="ui.activeTab === 'saved'" id="panel-saved" class="single" role="tabpanel" aria-labelledby="tab-saved">
      <SavedTripsList />
    </div>
  </main>

  <RatingDialog />

  <AppDialog :open="ui.dialog === 'help'" title="How Pathfinder works" @close="ui.closeDialog()">
    <div class="stack small help">
      <p>Every message goes down one path, shown in grey under each reply:</p>
      <ul>
        <li><strong>Plan · 4 agents</strong>: a new trip. Attraction, hotel, weather and ticket agents run in parallel.</li>
        <li><strong>Modify · hotel agent</strong>: a change. Only the affected agents re-run; locked (confirmed) stops are kept.</li>
        <li><strong>Quick question</strong>: answered from your plan or one tool call.</li>
        <li><strong>Needs clarification</strong>: something is missing; fill it in and press Continue.</li>
      </ul>
      <p>
        Fill in your trip on the left and press Generate, or just type in the chat. Select a day, stop, hotel or
        ticket in the plan to ask about just that part (the chat shows “About: …”); select it again or press
        Escape to clear. A selected stop can also be edited directly: its times, note and day, delete, or lock.
      </p>
      <p>Facts from tools carry a timestamp. A grey “unavailable – refresh” badge re-checks just that part.</p>
      <p class="muted">
        Backend:
        <template v-if="session.backend.online === true">
          online (storage {{ session.backend.info.storage_backend }}, router {{ session.backend.info.router_provider }},
          agents {{ session.backend.info.agent_llm_provider }})
        </template>
        <template v-else-if="session.backend.online === false">unreachable</template>
        <template v-else>checking…</template>
      </p>
      <div class="row">
        <button type="button" class="btn btn-small" :disabled="session.busy || session.loading" @click="session.newSession(); ui.closeDialog()">
          Start a new session
        </button>
      </div>
    </div>
  </AppDialog>
</template>

<style scoped>
.skip-link {
  position: absolute;
  left: 8px;
  top: -48px;
  z-index: 60;
  padding: 8px 14px;
  border-radius: var(--radius-pill);
  background: var(--primary);
  color: var(--primary-text);
}

.skip-link:focus {
  top: 8px;
}

.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2) var(--space-4);
  max-width: 1560px;
  margin: 0 auto var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--danger-soft);
  color: var(--danger);
}

main {
  max-width: 1560px;
  margin: 0 auto;
  padding: 0 var(--space-6) var(--space-8);
}

/* Three columns >= 1280px, two columns >= 900px, one column below (input, chat, plan, map). */
.dashboard {
  display: grid;
  grid-template-columns: minmax(320px, 0.95fr) minmax(0, 1.3fr) minmax(300px, 0.95fr);
  grid-template-areas: 'left center right';
  align-items: start;
  gap: var(--space-5);
}

.col {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

.col-left {
  grid-area: left;
}

.col-center {
  grid-area: center;
}

.col-right {
  grid-area: right;
}

.single {
  min-width: 0;
}

.help ul {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding-left: 18px;
  list-style: disc;
}

@media (max-width: 1279px) {
  .dashboard {
    grid-template-columns: minmax(300px, 1fr) minmax(0, 1.2fr);
    grid-template-areas:
      'left center'
      'left right';
  }
}

@media (max-width: 899px) {
  main {
    padding: 0 var(--space-4) var(--space-6);
  }

  .dashboard {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      'left'
      'center'
      'right';
    gap: var(--space-4);
  }

  .col {
    gap: var(--space-4);
  }

  .banner {
    margin: 0 var(--space-4) var(--space-3);
  }
}
</style>
