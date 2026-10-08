<script setup lang="ts">
import { Layers, Minus, Plus, X } from 'lucide-vue-next'

/** Floating circular map controls, one group per corner. */
defineProps<{ canZoomIn: boolean; canZoomOut: boolean; routeLayer: boolean; canClose: boolean }>()
const emit = defineEmits<{ zoomIn: []; zoomOut: []; toggleLayers: []; close: [] }>()
</script>

<template>
  <div class="corner top-left">
    <button type="button" class="icon-btn ctl" aria-label="Clear the selected stop" :disabled="!canClose" @click="emit('close')">
      <X :size="16" aria-hidden="true" />
    </button>
  </div>
  <div class="corner top-right">
    <button
      type="button"
      class="icon-btn ctl"
      :aria-pressed="routeLayer"
      :aria-label="routeLayer ? 'Hide route and hotel layer' : 'Show route and hotel layer'"
      @click="emit('toggleLayers')"
    >
      <Layers :size="16" aria-hidden="true" />
    </button>
  </div>
  <div class="corner bottom-right stack-ctl">
    <button type="button" class="icon-btn ctl" aria-label="Zoom in" :disabled="!canZoomIn" @click="emit('zoomIn')">
      <Plus :size="16" aria-hidden="true" />
    </button>
    <button type="button" class="icon-btn ctl" aria-label="Zoom out" :disabled="!canZoomOut" @click="emit('zoomOut')">
      <Minus :size="16" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.corner {
  position: absolute;
  z-index: 1;
}

.top-left {
  top: 12px;
  left: 12px;
}

.top-right {
  top: 12px;
  right: 12px;
}

.bottom-right {
  right: 12px;
  bottom: 12px;
}

.stack-ctl {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ctl {
  box-shadow: var(--shadow-float);
}

.ctl[aria-pressed='true'] {
  background: var(--primary);
  border-color: var(--primary);
  color: var(--primary-text);
}
</style>
