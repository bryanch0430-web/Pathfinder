<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import type { AgentName, QuickAnswer } from '@/api'
import { useSessionStore } from '@/stores/session'
import type { AgentProgress } from '@/stores/session'
import { AGENT_LABELS, AGENT_NAMES, EXAMPLE_PROMPTS, MAX_MESSAGE_LENGTH } from '@/lib/constants'
import { formatDateTime } from '@/lib/format'
import RouteBadge from './RouteBadge.vue'

const store = useSessionStore()
const uid = useId()

const draft = ref('')
const scroller = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)

const canSend = computed(() => !store.busy && !store.loading && draft.value.trim().length > 0)

const transportLabel = computed(() => {
  if (store.transport === 'stream') return 'Live stream'
  if (store.transport === 'http') return 'HTTP fallback (no live progress)'
  return ''
})

/** Agents only run for plan/modify; for ask/unclear the chips would be misleading. */
const showProgress = computed(() => {
  if (store.agentsActive) return true
  return store.busy && (store.currentRoute === null || store.currentRoute === 'plan' || store.currentRoute === 'modify')
})

function chipText(progress: AgentProgress): string {
  if (progress.phase === 'running') return 'running'
  if (progress.phase === 'finished') return progress.status ?? 'done'
  return store.busy ? 'waiting' : 'not re-run'
}

function chipClass(progress: AgentProgress): string {
  if (progress.phase === 'running') return 'badge-info'
  if (progress.phase === 'finished') {
    if (progress.status === 'unavailable') return 'badge-danger'
    if (progress.status === 'stale') return 'badge-warn'
    return 'badge-ok'
  }
  return ''
}

function chipTitle(agent: AgentName, progress: AgentProgress): string {
  return progress.note ? `${AGENT_LABELS[agent]}: ${progress.note}` : AGENT_LABELS[agent]
}

function answerNote(answer: QuickAnswer): string {
  if (answer.unavailable) return 'The live source was unavailable, so nothing was filled in from model knowledge.'
  if (answer.from_plan) return 'Answered from your current plan.'
  if (answer.tool_called) return 'Looked up live.'
  return ''
}

function sourceLine(answer: QuickAnswer): string {
  return (answer.sources ?? [])
    .map((source) => `${source.tool} via ${source.provider}, fetched ${formatDateTime(source.fetched_at)}`)
    .join('; ')
}

async function submit(): Promise<void> {
  if (!canSend.value) return
  const text = draft.value
  draft.value = ''
  await store.send(text)
}

function onEnter(event: KeyboardEvent): void {
  // Enter confirms an IME composition (Chinese/Japanese input); it must not send the message.
  if (event.isComposing) return
  event.preventDefault()
  void submit()
}

function useExample(prompt: string): void {
  draft.value = prompt
  input.value?.focus()
}

watch(
  () => [store.log.length, store.busy] as const,
  async () => {
    await nextTick()
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  },
)
</script>

<template>
  <section class="card chat" aria-labelledby="chat-title">
    <header>
      <h2 id="chat-title">Chat</h2>
      <span v-if="transportLabel" class="small muted">{{ transportLabel }}</span>
    </header>

    <div
      ref="scroller"
      class="messages"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      aria-label="Conversation"
      tabindex="0"
    >
      <p v-if="store.log.length === 0" class="empty">
        Fill in the trip details, then ask Pathfinder to plan your trip.
      </p>

      <article
        v-for="entry in store.log"
        :key="entry.id"
        class="msg"
        :class="[`msg-${entry.role}`, `kind-${entry.kind}`]"
      >
        <header class="msg-head">
          <span class="who">{{ entry.role === 'user' ? 'You' : 'Pathfinder' }}</span>
          <RouteBadge v-if="entry.role === 'assistant' && entry.route" :route="entry.route" />
          <span v-if="entry.kind === 'clarification'" class="badge badge-warn">Needs more info</span>
          <span v-if="entry.kind === 'error'" class="badge badge-danger">Error</span>
          <span
            v-if="entry.flagged"
            class="badge badge-warn"
            title="The safety screen flagged this message. It was treated as plain data, not as instructions."
          >
            Flagged input
          </span>
        </header>

        <p class="body">{{ entry.content }}</p>

        <div v-if="entry.missingFields.length > 0" class="missing">
          <span class="small">Missing:</span>
          <ul class="chips">
            <li v-for="field in entry.missingFields" :key="field" class="badge badge-warn">
              {{ field.replace(/_/g, ' ') }}
            </li>
          </ul>
        </div>

        <template v-if="entry.answer">
          <p v-if="answerNote(entry.answer)" class="small muted">{{ answerNote(entry.answer) }}</p>
          <p v-if="sourceLine(entry.answer)" class="small muted">Source: {{ sourceLine(entry.answer) }}</p>
        </template>

        <p v-if="entry.agentsRun.length > 0" class="small muted">
          Ran: {{ entry.agentsRun.map((agent) => AGENT_LABELS[agent]).join(', ') }}
        </p>
      </article>

      <p v-if="store.busy" class="working small muted" role="status">Pathfinder is working...</p>
    </div>

    <div v-if="showProgress" class="progress" aria-label="Agent progress">
      <div class="row">
        <span class="small muted">Agents</span>
        <RouteBadge v-if="store.currentRoute" :route="store.currentRoute" />
      </div>
      <ul class="chips" aria-live="polite">
        <li
          v-for="agent in AGENT_NAMES"
          :key="agent"
          class="badge chip"
          :class="chipClass(store.agents[agent])"
          :title="chipTitle(agent, store.agents[agent])"
        >
          <span v-if="store.agents[agent].phase === 'running'" class="spinner" aria-hidden="true"></span>
          {{ AGENT_LABELS[agent] }}: {{ chipText(store.agents[agent]) }}
        </li>
      </ul>
    </div>

    <form class="composer" @submit.prevent="submit">
      <label :for="`${uid}-message`" class="sr-only">Message Pathfinder</label>
      <textarea
        :id="`${uid}-message`"
        ref="input"
        v-model="draft"
        rows="2"
        :maxlength="MAX_MESSAGE_LENGTH"
        :disabled="store.loading"
        placeholder="Ask Pathfinder to plan, change the plan, or look something up (Enter to send, Shift+Enter for a new line)"
        @keydown.enter.exact="onEnter"
      ></textarea>
      <button type="submit" class="btn btn-primary" :disabled="!canSend">
        {{ store.busy ? 'Working...' : 'Send' }}
      </button>
    </form>

    <div class="examples" role="group" aria-label="Example prompts">
      <span class="small muted">Try:</span>
      <button
        v-for="prompt in EXAMPLE_PROMPTS"
        :key="prompt"
        type="button"
        class="btn btn-small"
        :disabled="store.busy"
        @click="useExample(prompt)"
      >
        {{ prompt }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat > header {
  margin-bottom: 0;
}

.messages {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 160px;
  max-height: 440px;
  overflow-y: auto;
  padding: 4px;
}

.msg {
  max-width: 92%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
}

.msg-user {
  align-self: flex-end;
  background: var(--primary-soft);
  border-color: transparent;
}

.msg-assistant {
  align-self: flex-start;
}

/* Clarification replies are visually distinct: dashed amber border plus a label, not colour alone. */
.kind-clarification {
  background: var(--warn-soft);
  border: 2px dashed var(--warn);
}

.kind-error {
  background: var(--danger-soft);
  border-color: var(--danger);
}

.msg-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.who {
  font-size: 0.8rem;
  font-weight: 700;
}

.body {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.missing {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.progress {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}

.progress .chips {
  margin-top: 6px;
}

.chip {
  font-size: 0.8rem;
}

.spinner {
  width: 10px;
  height: 10px;
  border: 2px solid currentcolor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 8px;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
</style>
