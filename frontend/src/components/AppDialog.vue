<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { X } from 'lucide-vue-next'

/**
 * Accessible modal: labelled by its title, Escape and the close button close it, Tab is kept
 * inside, and focus returns to whatever opened it.
 */
const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ close: [] }>()

const titleId = `${useId()}-title`
const panel = ref<HTMLElement | null>(null)
let returnFocus: HTMLElement | null = null

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusables(): HTMLElement[] {
  return Array.from(panel.value?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      await nextTick()
      const [, firstContent] = focusables() // skip the close button
      ;(firstContent ?? focusables()[0] ?? panel.value)?.focus()
    } else if (returnFocus) {
      returnFocus.focus()
      returnFocus = null
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => returnFocus?.focus())

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    emit('close')
    return
  }
  if (event.key !== 'Tab') return
  const list = focusables()
  if (list.length === 0) return
  const first = list[0]!
  const last = list[list.length - 1]!
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <Teleport to="body" :disabled="!open">
    <div v-if="open" class="backdrop" @mousedown.self="emit('close')">
      <div
        ref="panel"
        class="panel card"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        tabindex="-1"
        @keydown="onKeydown"
      >
        <header class="card-header">
          <h2 :id="titleId">{{ title }}</h2>
          <button type="button" class="icon-btn icon-btn-sm" aria-label="Close dialog" @click="emit('close')">
            <X :size="16" aria-hidden="true" />
          </button>
        </header>
        <slot />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: var(--space-4);
  background: rgb(31 29 27 / 35%);
}

.panel {
  width: min(560px, 100%);
  max-height: calc(100vh - 32px);
  overflow-y: auto;
  box-shadow: var(--shadow-float);
}
</style>
