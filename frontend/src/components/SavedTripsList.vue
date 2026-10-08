<script setup lang="ts">
import { computed } from 'vue'
import { Bookmark } from 'lucide-vue-next'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'

/**
 * PLACEHOLDER: the API has no endpoint that lists saved trips (useful plans are written to the
 * long-term store by the background writer and only read back as similar-trip hints for the
 * planner). This shows what this session knows; see DECISIONS.md, frontend section.
 */
const planStore = usePlanStore()
const session = useSessionStore()

const refs = computed(() => planStore.plan?.saved_trip_refs ?? [])
const outcome = computed(() => session.feedbackOutcome)
</script>

<template>
  <section class="card saved" aria-labelledby="saved-title">
    <header class="card-header">
      <h2 id="saved-title">Saved trips</h2>
      <span class="badge">Placeholder</span>
    </header>

    <div class="empty">
      <Bookmark :size="22" aria-hidden="true" />
      <p>Browsing saved trips needs a list endpoint the API does not offer yet.</p>
    </div>

    <ul class="facts small">
      <li v-if="outcome">
        This session saved plan version {{ outcome.version }} as useful ({{ outcome.rating }}/5){{ outcome.queued ? ', queued for the long-term store' : '' }}.
      </li>
      <li v-else>Mark a plan as useful to save it to your long-term memory.</li>
      <li v-if="planStore.hasPlan">
        The current plan drew on {{ refs.length }} similar saved {{ refs.length === 1 ? 'trip' : 'trips' }}.
      </li>
    </ul>
  </section>
</template>

<style scoped>
.saved {
  max-width: 720px;
  margin: 0 auto;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
}

.facts {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-4);
  padding-left: 18px;
  list-style: disc;
  color: var(--text-muted);
}
</style>
