<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Element as ApiElement } from '../lib/api'

const props = defineProps<{
  width: number | null
  height: number | null
  backgroundUrl: string | null
  elements: ApiElement[]
  layerUrls: Record<string, string>
}>()

const aspectStyle = computed(() => {
  if (!props.width || !props.height) return undefined
  return { aspectRatio: `${props.width} / ${props.height}` }
})

const orderedElements = computed<ApiElement[]>(() =>
  [...props.elements].sort((a, b) => a.z_order - b.z_order),
)

const layerEls = ref<Record<string, HTMLImageElement>>({})

function setLayerRef(id: string) {
  return (el: unknown): void => {
    if (el instanceof HTMLImageElement) {
      layerEls.value[id] = el
    }
  }
}

defineExpose({
  getLayerRefs: (): Record<string, HTMLImageElement> => ({ ...layerEls.value }),
})
</script>

<template>
  <div
    class="relative w-full max-w-3xl mx-auto"
    :style="aspectStyle"
    data-testid="layered-canvas"
  >
    <img
      v-if="backgroundUrl"
      :src="backgroundUrl"
      alt="Background"
      class="absolute inset-0 w-full h-full object-contain select-none"
      data-testid="layer-background"
      draggable="false"
    />
    <img
      v-for="el in orderedElements"
      v-show="layerUrls[el.id]"
      :key="el.id"
      :ref="setLayerRef(el.id)"
      :src="layerUrls[el.id]"
      :alt="el.label"
      class="absolute inset-0 w-full h-full object-contain select-none pointer-events-none"
      :data-testid="`layer-${el.id}`"
      :data-z-order="el.z_order"
      draggable="false"
    />
    <slot :width="width" :height="height" />
  </div>
</template>
