<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import {
  API_BASE_URL,
  ApiError,
  type Element,
  inpaintBackground,
  type Layer,
  perceiveElements,
  planAnimation,
  segmentElements,
} from '../lib/api'
import { buildTimeline, type AnimationDSL } from '../lib/animator'
import { downloadBlob, exportGif } from '../lib/gifExporter'
import LayeredCanvas from '../components/LayeredCanvas.vue'

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

const canvasRef = ref<InstanceType<typeof LayeredCanvas> | null>(null)

const animationPrompt = ref('')
const isPlanning = ref(false)
const planError = ref<string | null>(null)
const hasTimeline = ref(false)
const isPlaying = ref(false)
const isExporting = ref(false)
const exportProgress = ref(0)
const exportError = ref<string | null>(null)
let currentTimeline: gsap.core.Timeline | null = null

// Element ids to hide on the canvas while an animation is active. Populated
// whenever a plan references a child element — we hide that child's parent
// so the user doesn't see the arm in two places at once (static parent +
// moving child).
const hiddenParentIds = ref<string[]>([])

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
  let maxX = 1
  let maxY = 1
  for (const el of elements.value) {
    const [x, y, w, h] = el.bbox
    if (x + w > maxX) maxX = x + w
    if (y + h > maxY) maxY = y + h
  }
  return `0 0 ${maxX} ${maxY}`
})

const pipelineReady = computed(
  () => steps.value.every((s) => s.status === 'success') && elements.value.length > 0,
)

const canMakeLively = computed(
  () => pipelineReady.value && animationPrompt.value.trim().length > 0 && !isPlanning.value,
)

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
    // Build a contour lookup from the segment response so inpaint can draw
    // precise masks instead of bounding-box rectangles.
    const contourByElement: Record<string, Array<[number, number]> | null> = {}
    for (const layer of layers.value) {
      contourByElement[layer.element_id] = layer.contour ?? null
    }
    const response = await inpaintBackground(
      props.imageId,
      elements.value.map((el) => ({
        bbox: el.bbox,
        contour: contourByElement[el.id] ?? null,
      })),
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

function disposeTimeline(): void {
  if (currentTimeline) {
    currentTimeline.kill()
    currentTimeline = null
  }
  hasTimeline.value = false
  isPlaying.value = false
  // Re-show any parents we'd hidden during playback so the canvas returns
  // to its pre-animation appearance.
  hiddenParentIds.value = []
}

/**
 * Compute which parent elements should be hidden while `plan` is playing.
 *
 * Rule: if the plan animates a child element (has a parent_id), hide that
 * parent so the child's motion doesn't ghost against a static copy.
 * Non-animated siblings stay visible — they cover the rest of the parent's
 * silhouette as long as the VLM's decomposition tiles it reasonably.
 */
function computeHiddenParents(plan: AnimationDSL): string[] {
  const animatedIds = new Set(plan.map((ea) => ea.element_id))
  const parents = new Set<string>()
  for (const el of elements.value) {
    if (el.parent_id && animatedIds.has(el.id)) {
      parents.add(el.parent_id)
    }
  }
  return [...parents]
}

function resetLayerTransforms(): void {
  const refs = canvasRef.value?.getLayerRefs() ?? {}
  for (const key of Object.keys(refs)) {
    const el = refs[key]
    if (el) {
      el.style.transform = ''
      el.style.opacity = ''
    }
  }
}

async function onMakeLively(): Promise<void> {
  if (!canMakeLively.value) return
  isPlanning.value = true
  planError.value = null
  try {
    const response = await planAnimation(
      props.imageId,
      elements.value,
      animationPrompt.value.trim(),
    )
    const refs = canvasRef.value?.getLayerRefs() ?? {}
    const dsl: AnimationDSL = response.plan
    // Only include animations for elements that have rendered layer refs.
    const usable = dsl.filter((ea) => refs[ea.element_id] != null)
    if (usable.length === 0) {
      throw new Error('No matching layers available to animate')
    }
    disposeTimeline()
    resetLayerTransforms()
    // Pass intrinsic image dims so pivots (image-space px) can be mapped
    // to CSS transform-origin percentages. When dims are not yet loaded
    // (canvasDims still null) the animator silently falls back to
    // centre-based rotation, which matches pre-M1.5 behaviour.
    const timeline = buildTimeline(usable, refs, {
      paused: true,
      imageWidth: canvasDims.value?.width,
      imageHeight: canvasDims.value?.height,
    })
    timeline.eventCallback('onComplete', () => {
      isPlaying.value = false
    })
    currentTimeline = timeline
    hasTimeline.value = true
    // Hide parents of any animated child so the motion doesn't ghost.
    hiddenParentIds.value = computeHiddenParents(usable)
    timeline.play()
    isPlaying.value = true
  } catch (err) {
    planError.value = formatError(err)
  } finally {
    isPlanning.value = false
  }
}

function onPlay(): void {
  if (!currentTimeline) return
  currentTimeline.play()
  isPlaying.value = true
}

function onPause(): void {
  if (!currentTimeline) return
  currentTimeline.pause()
  isPlaying.value = false
}

function onReset(): void {
  if (!currentTimeline) return
  currentTimeline.pause(0)
  isPlaying.value = false
}

async function onExportGif(): Promise<void> {
  if (!currentTimeline || !canvasDims.value || isExporting.value) return
  const refs = canvasRef.value?.getLayerRefs() ?? {}
  const sample = Object.values(refs).find(
    (el): el is HTMLImageElement => el instanceof HTMLImageElement,
  )
  const renderedWidth = sample?.clientWidth ?? canvasDims.value.width
  const renderedHeight = sample?.clientHeight ?? canvasDims.value.height
  if (renderedWidth <= 0 || renderedHeight <= 0) {
    exportError.value = 'Canvas is not visible yet'
    return
  }

  isExporting.value = true
  exportProgress.value = 0
  exportError.value = null
  isPlaying.value = false

  try {
    const blob = await exportGif(
      {
        timeline: currentTimeline,
        intrinsicWidth: canvasDims.value.width,
        intrinsicHeight: canvasDims.value.height,
        renderedWidth,
        renderedHeight,
        backgroundUrl: backgroundUrl.value,
        elements: elements.value,
        layerUrls: layerByElement.value,
        layerRefs: refs,
      },
      {
        fps: 15,
        minDurationSeconds: 2,
        onProgress: (fraction) => {
          exportProgress.value = fraction
        },
      },
    )
    downloadBlob(blob, 'make-it-lively.gif')
  } catch (err) {
    exportError.value = formatError(err)
  } finally {
    isExporting.value = false
  }
}

onMounted(() => {
  void runPipelineFrom('perception')
})

onBeforeUnmount(() => {
  disposeTimeline()
})
</script>

<template>
  <main class="min-h-screen bg-slate-950 text-slate-100">
    <header class="border-b border-slate-800 px-6 py-4 flex items-center justify-between">
      <h1 class="text-xl font-semibold">Make It Lively — Editor</h1>
      <p class="text-xs text-slate-500 font-mono">{{ props.imageId }}</p>
    </header>

    <div class="grid grid-cols-[280px_1fr_320px] gap-0 min-h-[calc(100vh-65px)]">
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
          <li
            v-for="el in elements"
            :key="el.id"
            :class="el.parent_id ? 'pl-4 border-l border-slate-800 ml-2' : ''"
          >
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
        <LayeredCanvas
          v-else
          ref="canvasRef"
          :width="canvasDims?.width ?? null"
          :height="canvasDims?.height ?? null"
          :background-url="backgroundUrl"
          :elements="elements"
          :layer-urls="layerByElement"
          :hidden-element-ids="hiddenParentIds"
          data-testid="canvas"
        >
          <svg
            class="absolute inset-0 w-full h-full pointer-events-none"
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
        </LayeredCanvas>
      </section>

      <aside
        class="border-l border-slate-800 p-4 overflow-y-auto flex flex-col gap-4"
        data-testid="animation-panel"
      >
        <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Animate
        </h2>
        <label
          class="flex flex-col gap-2 text-sm"
          for="animation-prompt"
        >
          <span class="text-slate-300">Describe what should happen</span>
          <textarea
            id="animation-prompt"
            v-model="animationPrompt"
            rows="5"
            :disabled="!pipelineReady || isPlanning"
            placeholder="e.g. The bird flaps its wings while drifting across the sky."
            class="rounded-lg bg-slate-900/60 border border-slate-800 focus:border-indigo-400 focus:outline-none p-3 resize-none text-sm disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="animation-prompt"
          />
        </label>

        <button
          type="button"
          class="rounded-lg bg-indigo-500 hover:bg-indigo-400 text-white font-medium py-2 px-4 transition-colors disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed"
          :disabled="!canMakeLively"
          data-testid="make-lively"
          @click="onMakeLively"
        >
          {{ isPlanning ? 'Planning…' : 'Make it Lively' }}
        </button>

        <p
          v-if="planError"
          class="text-xs text-red-200 bg-red-500/10 border border-red-500/40 rounded-md p-2"
          role="alert"
          data-testid="animation-error"
        >
          {{ planError }}
        </p>

        <div class="border-t border-slate-800 pt-4">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-3">
            Playback
          </h3>
          <div class="grid grid-cols-3 gap-2">
            <button
              type="button"
              class="rounded-md border border-slate-700 bg-slate-900/60 hover:bg-slate-800 py-2 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="!hasTimeline || isPlaying"
              data-testid="playback-play"
              @click="onPlay"
            >
              Play
            </button>
            <button
              type="button"
              class="rounded-md border border-slate-700 bg-slate-900/60 hover:bg-slate-800 py-2 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="!hasTimeline || !isPlaying"
              data-testid="playback-pause"
              @click="onPause"
            >
              Pause
            </button>
            <button
              type="button"
              class="rounded-md border border-slate-700 bg-slate-900/60 hover:bg-slate-800 py-2 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="!hasTimeline"
              data-testid="playback-reset"
              @click="onReset"
            >
              Reset
            </button>
          </div>

          <div class="mt-4 flex flex-col gap-2">
            <button
              type="button"
              class="rounded-md bg-emerald-500 hover:bg-emerald-400 text-white font-medium py-2 px-4 transition-colors disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed"
              :disabled="!hasTimeline || isExporting"
              data-testid="export-gif"
              @click="onExportGif"
            >
              {{ isExporting ? 'Exporting…' : 'Export GIF' }}
            </button>
            <div
              v-if="isExporting"
              class="flex flex-col gap-1"
              data-testid="export-progress"
            >
              <div class="h-2 w-full overflow-hidden rounded bg-slate-800">
                <div
                  class="h-full bg-emerald-400 transition-[width] duration-150"
                  :style="{ width: `${Math.round(exportProgress * 100)}%` }"
                />
              </div>
              <p class="text-xs text-slate-400">
                Rendering frames… {{ Math.round(exportProgress * 100) }}%
              </p>
            </div>
            <p
              v-if="exportError"
              class="text-xs text-red-200 bg-red-500/10 border border-red-500/40 rounded-md p-2"
              role="alert"
              data-testid="export-error"
            >
              {{ exportError }}
            </p>
          </div>
        </div>
      </aside>
    </div>
  </main>
</template>
