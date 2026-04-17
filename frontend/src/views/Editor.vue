<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  API_BASE_URL,
  ApiError,
  type Element,
  inpaintBackground,
  type Layer,
  perceiveElements,
  segmentElements,
} from '../lib/api'

const props = defineProps<{ imageId: string }>()

type StepStatus = 'pending' | 'running' | 'success' | 'error'

interface StepState {
  key: 'perception' | 'segment' | 'inpaint'
  label: string
  status: StepStatus
  error: string | null
}

const steps = ref<StepState[]>([
  { key: 'perception', label: 'Detecting elements', status: 'pending', error: null },
  { key: 'segment', label: 'Extracting layers', status: 'pending', error: null },
  { key: 'inpaint', label: 'Filling background', status: 'pending', error: null },
])

const elements = ref<Element[]>([])
const layers = ref<Layer[]>([])
const backgroundUrl = ref<string | null>(null)
const selectedId = ref<string | null>(null)
const canvasDims = ref<{ width: number; height: number } | null>(null)

const layerByElement = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const layer of layers.value) {
    map[layer.element_id] = `${API_BASE_URL}${layer.url}`
  }
  return map
})

const viewBox = computed<string>(() => {
  if (canvasDims.value) {
    return `0 0 ${canvasDims.value.width} ${canvasDims.value.height}`
  }
  // Fallback: derive from bbox extents so outlines still show pre-inpaint.
  let maxX = 1
  let maxY = 1
  for (const el of elements.value) {
    const [x, y, w, h] = el.bbox
    if (x + w > maxX) maxX = x + w
    if (y + h > maxY) maxY = y + h
  }
  return `0 0 ${maxX} ${maxY}`
})

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return 'Network error — is the backend running?'
    if (err.body && typeof err.body === 'object' && 'detail' in err.body) {
      const detail = (err.body as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
    }
    return `Request failed (${err.status})`
  }
  if (err instanceof Error) return err.message
  return 'Request failed'
}

function stepByKey(key: StepState['key']): StepState {
  const step = steps.value.find((s) => s.key === key)
  if (!step) throw new Error(`Unknown step: ${key}`)
  return step
}

function resetDownstream(key: StepState['key']): void {
  const order: StepState['key'][] = ['perception', 'segment', 'inpaint']
  const startIndex = order.indexOf(key)
  for (let i = startIndex; i < order.length; i++) {
    const step = stepByKey(order[i])
    step.status = 'pending'
    step.error = null
  }
}

async function runPerception(): Promise<boolean> {
  const step = stepByKey('perception')
  step.status = 'running'
  step.error = null
  try {
    const response = await perceiveElements(props.imageId)
    elements.value = response.elements
    step.status = 'success'
    return true
  } catch (err) {
    step.status = 'error'
    step.error = formatError(err)
    return false
  }
}

async function runSegment(): Promise<boolean> {
  const step = stepByKey('segment')
  step.status = 'running'
  step.error = null
  try {
    const response = await segmentElements(props.imageId, elements.value)
    layers.value = response.layers
    step.status = 'success'
    // Preload the first layer to resolve intrinsic dimensions for the canvas.
    if (response.layers.length > 0) {
      void preloadDims(`${API_BASE_URL}${response.layers[0].url}`)
    }
    return true
  } catch (err) {
    step.status = 'error'
    step.error = formatError(err)
    return false
  }
}

async function runInpaint(): Promise<boolean> {
  const step = stepByKey('inpaint')
  step.status = 'running'
  step.error = null
  try {
    const response = await inpaintBackground(
      props.imageId,
      elements.value.map((el) => ({ bbox: el.bbox })),
    )
    backgroundUrl.value = `${API_BASE_URL}${response.background_url}`
    step.status = 'success'
    void preloadDims(backgroundUrl.value)
    return true
  } catch (err) {
    step.status = 'error'
    step.error = formatError(err)
    return false
  }
}

function preloadDims(url: string): Promise<void> {
  return new Promise<void>((resolve) => {
    const img = new Image()
    img.onload = () => {
      canvasDims.value = { width: img.naturalWidth, height: img.naturalHeight }
      resolve()
    }
    img.onerror = () => resolve()
    img.src = url
  })
}

async function runPipelineFrom(startKey: StepState['key']): Promise<void> {
  resetDownstream(startKey)
  const order: StepState['key'][] = ['perception', 'segment', 'inpaint']
  for (const key of order.slice(order.indexOf(startKey))) {
    let ok = false
    if (key === 'perception') ok = await runPerception()
    else if (key === 'segment') ok = await runSegment()
    else if (key === 'inpaint') ok = await runInpaint()
    if (!ok) return
  }
}

function onRetry(key: StepState['key']): void {
  void runPipelineFrom(key)
}

function onSelect(id: string): void {
  selectedId.value = selectedId.value === id ? null : id
}

onMounted(() => {
  void runPipelineFrom('perception')
})
</script>

<template>
  <main class="min-h-screen bg-slate-950 text-slate-100">
    <header class="border-b border-slate-800 px-6 py-4 flex items-center justify-between">
      <h1 class="text-xl font-semibold">Make It Lively — Editor</h1>
      <p class="text-xs text-slate-500 font-mono">{{ props.imageId }}</p>
    </header>

    <div class="grid grid-cols-[280px_1fr] gap-0 min-h-[calc(100vh-65px)]">
      <aside
        class="border-r border-slate-800 p-4 overflow-y-auto"
        data-testid="element-panel"
      >
        <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-400 mb-3">
          Pipeline
        </h2>
        <ul class="space-y-2 mb-6" data-testid="pipeline-steps">
          <li
            v-for="step in steps"
            :key="step.key"
            class="rounded-lg border p-3 text-sm"
            :class="{
              'border-slate-700 bg-slate-900/50': step.status === 'pending',
              'border-indigo-500/50 bg-indigo-500/10': step.status === 'running',
              'border-emerald-500/40 bg-emerald-500/10': step.status === 'success',
              'border-red-500/40 bg-red-500/10': step.status === 'error',
            }"
            :data-testid="`step-${step.key}`"
            :data-status="step.status"
          >
            <div class="flex items-center justify-between">
              <span class="font-medium">{{ step.label }}</span>
              <span class="text-xs uppercase tracking-wide text-slate-400">
                {{ step.status }}
              </span>
            </div>
            <p
              v-if="step.status === 'error' && step.error"
              class="text-xs text-red-200 mt-2"
              role="alert"
            >
              {{ step.error }}
            </p>
            <button
              v-if="step.status === 'error'"
              type="button"
              class="mt-2 text-xs underline text-red-200 hover:text-red-100"
              :data-testid="`retry-${step.key}`"
              @click="onRetry(step.key)"
            >
              Retry
            </button>
          </li>
        </ul>

        <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-400 mb-3">
          Elements
        </h2>
        <ul
          v-if="stepByKey('perception').status !== 'success' || elements.length === 0"
          class="space-y-2"
          data-testid="element-skeletons"
        >
          <li
            v-for="i in 3"
            :key="i"
            class="h-14 rounded-lg bg-slate-900/50 border border-slate-800 animate-pulse"
          />
        </ul>
        <ul v-else class="space-y-2" data-testid="element-list">
          <li v-for="el in elements" :key="el.id">
            <button
              type="button"
              class="w-full flex items-center gap-3 rounded-lg border p-2 text-left transition-colors"
              :class="
                selectedId === el.id
                  ? 'border-indigo-400 bg-indigo-500/10'
                  : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'
              "
              :data-testid="`element-${el.id}`"
              @click="onSelect(el.id)"
            >
              <div
                class="w-10 h-10 rounded bg-slate-800 bg-center bg-contain bg-no-repeat flex-shrink-0"
                :class="{ 'animate-pulse': !layerByElement[el.id] }"
                :style="
                  layerByElement[el.id]
                    ? { backgroundImage: `url(${layerByElement[el.id]})` }
                    : undefined
                "
              />
              <span class="text-sm font-medium truncate">{{ el.label }}</span>
            </button>
          </li>
        </ul>
      </aside>

      <section class="p-6 flex items-center justify-center" data-testid="canvas-area">
        <div
          v-if="elements.length === 0"
          class="w-full max-w-2xl aspect-video rounded-xl border border-dashed border-slate-800 bg-slate-900/40 animate-pulse"
          data-testid="canvas-skeleton"
        />
        <div
          v-else
          class="relative w-full max-w-3xl"
          :style="
            canvasDims
              ? { aspectRatio: `${canvasDims.width} / ${canvasDims.height}` }
              : undefined
          "
          data-testid="canvas"
        >
          <img
            v-if="backgroundUrl"
            :src="backgroundUrl"
            alt="Background"
            class="absolute inset-0 w-full h-full object-contain rounded-lg"
          />
          <svg
            class="absolute inset-0 w-full h-full"
            :viewBox="viewBox"
            preserveAspectRatio="xMidYMid meet"
          >
            <rect
              v-for="el in elements"
              :key="el.id"
              :x="el.bbox[0]"
              :y="el.bbox[1]"
              :width="el.bbox[2]"
              :height="el.bbox[3]"
              fill="none"
              :stroke="selectedId === el.id ? '#818cf8' : 'rgba(148,163,184,0.4)'"
              :stroke-width="selectedId === el.id ? 4 : 2"
              :stroke-dasharray="selectedId === el.id ? undefined : '6 4'"
              :data-testid="`bbox-${el.id}`"
            />
          </svg>
        </div>
      </section>
    </div>
  </main>
</template>
