<script setup lang="ts">
import { ref, watch } from 'vue'
import { Star } from 'lucide-vue-next'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import AppDialog from './AppDialog.vue'

/** "Mark as useful": a 1–5 star rating posted to POST /plan/feedback (useful = true). */
const session = useSessionStore()
const ui = useUiStore()

const rating = ref(0)
const error = ref('')

watch(
  () => ui.dialog,
  (dialog) => {
    if (dialog === 'rating') {
      rating.value = 0
      error.value = ''
    }
  },
)

const STARS = [1, 2, 3, 4, 5] as const

async function save(): Promise<void> {
  if (rating.value < 1) {
    error.value = 'Choose a rating from 1 to 5 stars.'
    return
  }
  error.value = ''
  const ok = await session.submitFeedback(true, rating.value)
  if (ok) ui.closeDialog()
  else error.value = session.lastError ?? 'Could not save the rating.'
}
</script>

<template>
  <AppDialog :open="ui.dialog === 'rating'" title="Mark this plan as useful" @close="ui.closeDialog()">
    <form class="stack" @submit.prevent="save">
      <p class="small muted">
        Useful plans are saved to your long-term memory so similar future trips can build on them.
      </p>
      <fieldset>
        <legend>How useful is this plan?</legend>
        <div class="stars">
          <label v-for="value in STARS" :key="value" class="star" :class="{ on: value <= rating }">
            <input v-model="rating" class="sr-only" type="radio" name="rating" :value="value" />
            <Star :size="28" aria-hidden="true" />
            <span class="sr-only">{{ value }} {{ value === 1 ? 'star' : 'stars' }}</span>
          </label>
        </div>
      </fieldset>
      <p v-if="error" class="field-error" role="alert">{{ error }}</p>
      <div class="row">
        <button type="submit" class="btn btn-primary" :disabled="session.sendingFeedback">
          {{ session.sendingFeedback ? 'Saving…' : 'Save' }}
        </button>
        <button type="button" class="btn" @click="ui.closeDialog()">Cancel</button>
      </div>
    </form>
  </AppDialog>
</template>

<style scoped>
.stars {
  display: flex;
  gap: var(--space-1);
}

.star {
  display: inline-grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin: 0;
  border-radius: 50%;
  color: var(--icon-muted);
  cursor: pointer;
}

.star.on {
  color: var(--accent);
}

.star.on :deep(svg) {
  fill: var(--accent);
}

.star:focus-within {
  box-shadow: var(--focus-ring);
}
</style>
