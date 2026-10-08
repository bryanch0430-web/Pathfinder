<script setup lang="ts">
import { computed } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import type { ChatEntry } from '@/stores/session'
import { routeStatusLine } from '@/lib/status'

const props = defineProps<{ entry: ChatEntry }>()

const status = computed(() => {
  if (props.entry.role !== 'assistant') return ''
  if (props.entry.kind === 'error') return props.entry.route ? `${routeStatusLine(props.entry.route, props.entry.agentsRun)} · error` : 'Error'
  return routeStatusLine(props.entry.route, props.entry.agentsRun)
})
</script>

<template>
  <li class="message" :class="[entry.role, entry.kind]">
    <div class="bubble">
      <span class="sr-only">{{ entry.role === 'user' ? 'You said:' : 'Pathfinder replied:' }}</span>
      <p class="content">{{ entry.content }}</p>
      <p v-if="entry.flagged" class="flag tiny">
        <ShieldAlert :size="12" aria-hidden="true" />
        Flagged by the injection screen; handled as plain trip data.
      </p>
    </div>
    <p v-if="status" class="status" data-testid="route-status">{{ status }}</p>
  </li>
</template>

<style scoped>
.message {
  display: flex;
  flex-direction: column;
  max-width: 88%;
}

.message.user {
  align-self: flex-end;
  align-items: flex-end;
}

.message.assistant {
  align-self: flex-start;
}

.bubble {
  padding: 10px 14px;
  border-radius: 18px;
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.user .bubble {
  background: var(--primary);
  color: var(--primary-text);
  border-bottom-right-radius: 6px;
}

.assistant .bubble {
  background: var(--card-soft);
  border: 1px solid var(--border);
  border-bottom-left-radius: 6px;
}

.assistant.error .bubble {
  background: var(--danger-soft);
  border-color: transparent;
  color: var(--danger);
}

.content {
  white-space: pre-wrap;
}

.flag {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  color: var(--warn);
}

.status {
  margin-top: 4px;
  padding-left: 6px;
  font-size: var(--text-xs);
  color: var(--text-muted);
}
</style>
