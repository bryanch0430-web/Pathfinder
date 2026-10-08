<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const uid = useId()

const useful = ref(false)
const rating = ref<number | null>(null)
const hover = ref<number | null>(null)

const STARS = [1, 2, 3, 4, 5] as const

/** Feedback outcome for the plan version currently on screen (a new version can be rated again). */
const outcome = computed(() => {
  const result = store.feedbackOutcome
  const plan = store.plan
  if (!result || !plan) return null
  return result.planId === plan.plan_id && result.version === plan.version ? result : null
})

// Start from a clean form whenever the plan changes.
watch(
  () => (store.plan ? `${store.plan.plan_id}:${store.plan.version}` : ''),
  () => {
    useful.value = false
    rating.value = null
  },
)

const canSubmit = computed(
  () => store.plan !== null && rating.value !== null && !store.sendingFeedback,
)

const shown = computed(() => hover.value ?? rating.value ?? 0)

async function submit(): Promise<void> {
  if (rating.value === null) return
  await store.submitFeedback(useful.value, rating.value)
}
</script>

<template>
  <section class="card" aria-labelledby="rating-title">
    <header>
      <h2 id="rating-title">Was this plan useful?</h2>
    </header>

    <p v-if="!store.plan" class="empty">You can rate a plan once there is one.</p>

    <form v-else class="stack" @submit.prevent="submit">
      <div class="row">
        <button
          type="button"
          class="btn"
          :class="{ 'btn-primary': useful }"
          :aria-pressed="useful"
          @click="useful = !useful"
        >
          <span aria-hidden="true">{{ useful ? '✓' : '+' }}</span>
          {{ useful ? 'Marked as useful' : 'Mark as useful' }}
        </button>
        <span class="small muted">Useful plans are saved to help with similar trips later.</span>
      </div>

      <fieldset>
        <legend>Rating</legend>
        <div class="stars" @mouseleave="hover = null">
          <template v-for="star in STARS" :key="star">
            <input
              :id="`${uid}-star-${star}`"
              v-model="rating"
              class="sr-only"
              type="radio"
              :name="`${uid}-rating`"
              :value="star"
            />
            <label
              :for="`${uid}-star-${star}`"
              class="star"
              :class="{ on: star <= shown }"
              :title="`${star} of 5`"
              @mouseenter="hover = star"
            >
              <span aria-hidden="true">{{ star <= shown ? '★' : '☆' }}</span>
              <span class="sr-only">{{ star }} {{ star === 1 ? 'star' : 'stars' }}</span>
            </label>
          </template>
          <span class="small muted value" aria-live="polite">{{ rating ? `${rating} / 5` : 'Not rated' }}</span>
        </div>
      </fieldset>

      <div class="row">
        <button type="submit" class="btn btn-primary" :disabled="!canSubmit">
          {{ store.sendingFeedback ? 'Sending...' : 'Send feedback' }}
        </button>
        <span v-if="rating === null" class="small muted">Pick a rating to send feedback.</span>
      </div>

      <p v-if="outcome" class="result" role="status">
        <template v-if="outcome.queued">
          <span class="badge badge-ok">Saved for future trips</span>
          Thanks, this plan will help with similar requests.
        </template>
        <template v-else>
          <span class="badge">Feedback recorded</span>
          It was not saved for future trips{{ outcome.useful ? '' : ' because it was not marked as useful' }}.
        </template>
      </p>
    </form>
  </section>
</template>

<style scoped>
.stars {
  display: flex;
  align-items: center;
  gap: 2px;
}

.star {
  margin: 0;
  padding: 0 2px;
  font-size: 1.8rem;
  line-height: 1;
  color: var(--border-strong);
  cursor: pointer;
}

.star.on {
  color: var(--warn);
}

/* The radio inputs are visually hidden; show focus on the star that owns the focused radio. */
input:focus-visible + .star {
  outline: 3px solid var(--focus);
  outline-offset: 2px;
  border-radius: 4px;
}

.value {
  margin-left: 8px;
}

.result {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
</style>
