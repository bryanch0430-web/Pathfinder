<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowUp, LoaderCircle, X } from 'lucide-vue-next'
import { useSessionStore } from '@/stores/session'
import { usePlanStore } from '@/stores/plan'
import { useUiStore } from '@/stores/ui'
import { AGENT_LABELS, AGENT_NAMES, MAX_MESSAGE_LENGTH } from '@/lib/constants'
import { routeStatusLine } from '@/lib/status'
import ChatMessage from './ChatMessage.vue'
import ClarificationCard from './ClarificationCard.vue'
import QuickChips from './QuickChips.vue'

const session = useSessionStore()
const planStore = usePlanStore()
const ui = useUiStore()

const draft = ref('')
const input = ref<HTMLTextAreaElement | null>(null)
const list = ref<HTMLElement | null>(null)

/** Only the newest clarification can still be answered inline. */
const latestClarificationId = computed(() => {
  const last = session.log[session.log.length - 1]
  return last && last.kind === 'clarification' ? last.id : null
})

const destination = computed(() => planStore.plan?.destination ?? session.context.destination ?? null)

/** Status of the turn in flight, from the live stream events. */
const liveStatus = computed(() => {
  if (!session.currentRoute) return 'Routing…'
  const started = AGENT_NAMES.filter((agent) => session.agents[agent].phase !== 'idle')
  return routeStatusLine(session.currentRoute, started)
})

const liveAgents = computed(() =>
  AGENT_NAMES.filter((agent) => session.agents[agent].phase !== 'idle').map((agent) => ({
    agent,
    label: AGENT_LABELS[agent],
    ...session.agents[agent],
  })),
)

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || session.busy) return
  draft.value = ''
  const result = await session.send(text, { focus: true })
  // A message the server did not take (e.g. its focus was refused) goes back into an empty composer.
  if (result?.draft && !draft.value) draft.value = result.draft
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void send()
  } else if (event.key === 'Escape' && !event.isComposing && planStore.selection) {
    // Escape here clears the focus chip, the same as Escape in the workspace.
    planStore.clearSelection()
  }
}

function insert(text: string): void {
  draft.value = text
  void nextTick(() => {
    input.value?.focus()
    input.value?.setSelectionRange(text.length, text.length)
  })
}

async function scrollToEnd(): Promise<void> {
  await nextTick()
  const el = list.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(() => [session.log.length, session.busy, session.liveReply], scrollToEnd)
watch(
  () => ui.composerFocusRequest,
  async () => {
    await nextTick()
    input.value?.focus()
  },
)
</script>

<template>
  <section class="card chat" aria-labelledby="chat-title">
    <header class="chat-head">
      <h2 id="chat-title">Hello! Where are we going?</h2>
      <QuickChips :destination="destination" :disabled="session.busy" @insert="insert" />
    </header>

    <ol ref="list" class="messages" role="log" aria-live="polite" aria-label="Conversation">
      <li v-if="session.log.length === 0 && !session.busy" class="hint muted small">
        Tell me where and when you want to travel, for example “Plan 5 days in Kyoto”.
      </li>
      <template v-for="entry in session.log" :key="entry.id">
        <ClarificationCard
          v-if="entry.kind === 'clarification'"
          :entry="entry"
          :interactive="entry.id === latestClarificationId && !session.busy"
        />
        <ChatMessage v-else :entry="entry" />
      </template>

      <li v-if="session.busy" class="live" aria-busy="true">
        <div class="bubble">
          <p v-if="session.liveReply" class="content">{{ session.liveReply }}</p>
          <p v-else class="thinking">
            <LoaderCircle :size="14" class="spin" aria-hidden="true" />
            Working on it…
          </p>
          <ul v-if="liveAgents.length > 0" class="agents" aria-label="Agent progress">
            <li v-for="item in liveAgents" :key="item.agent" class="agent" :class="item.phase">
              {{ item.label }}{{ item.phase === 'running' ? '…' : item.status === 'ok' ? ' ✓' : ` · ${item.status ?? 'done'}` }}
            </li>
          </ul>
        </div>
        <p class="status" data-testid="live-status">{{ liveStatus }}</p>
      </li>
    </ol>

    <div v-if="planStore.selectionLabel" class="focus chip" data-testid="focus-chip">
      <span class="focus-label">About: {{ planStore.selectionLabel }}</span>
      <button
        type="button"
        class="focus-clear"
        :aria-label="`Stop asking about ${planStore.selectionLabel}`"
        title="Clear the selection"
        @click="planStore.clearSelection()"
      >
        <X :size="14" aria-hidden="true" />
      </button>
    </div>

    <form class="composer" @submit.prevent="send">
      <label for="chat-input" class="sr-only">Message Pathfinder</label>
      <textarea
        id="chat-input"
        ref="input"
        v-model="draft"
        rows="1"
        :maxlength="MAX_MESSAGE_LENGTH"
        placeholder="Ask anything about your trip…"
        autocomplete="off"
        @keydown="onKeydown"
      />
      <button
        type="submit"
        class="send"
        :disabled="session.busy || !draft.trim()"
        aria-label="Send message"
      >
        <ArrowUp :size="18" aria-hidden="true" />
      </button>
    </form>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.chat-head {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

h2 {
  font-size: var(--text-xl);
  letter-spacing: -0.01em;
}

.messages {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-height: 200px;
  max-height: 460px;
  overflow-y: auto;
  padding-right: 2px;
}

.hint {
  padding: var(--space-3);
}

.live {
  align-self: flex-start;
  max-width: 88%;
}

.live .bubble {
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: 18px;
  border-bottom-left-radius: 6px;
  background: var(--card-soft);
  font-size: var(--text-sm);
}

.content {
  white-space: pre-wrap;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.agents {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.agent {
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--card);
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.agent.finished {
  color: var(--text);
}

.status {
  margin-top: 4px;
  padding-left: 6px;
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* "About: Day 1 · Kiyomizu-dera ✕": the selection the next message is scoped to. */
.focus {
  align-self: flex-start;
  max-width: 100%;
  padding-right: 4px;
  border-color: transparent;
  background: var(--accent-soft);
  color: var(--accent-strong);
  cursor: default;
}

.focus-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.focus-clear {
  display: inline-grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.focus-clear:hover {
  background: var(--card);
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  padding: 6px 6px 6px 16px;
  border: 1px solid var(--border-strong);
  border-radius: 24px;
  background: var(--card);
}

.composer:focus-within {
  box-shadow: var(--focus-ring);
}

.composer textarea {
  flex: 1;
  min-height: 36px;
  max-height: 140px;
  padding: 7px 0;
  border: 0;
  background: transparent;
  resize: none;
}

.composer textarea:focus-visible {
  box-shadow: none;
}

.send {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: var(--primary);
  color: var(--primary-text);
  cursor: pointer;
}

.send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
