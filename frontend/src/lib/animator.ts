import gsap from 'gsap'

export type PrimitiveType =
  | 'translate'
  | 'rotate'
  | 'scale'
  | 'opacity'
  | 'path-follow'

export interface AnimationPrimitive {
  type: PrimitiveType
  dx?: number | null
  dy?: number | null
  angle?: number | null
  scale?: number | null
  opacity?: number | null
  path?: Array<[number, number]> | null
  duration_ms?: number | null
  easing?: string | null
  // Per-step pivot in image-space pixel coords. Converted to CSS
  // transform-origin on rotate / scale steps. Null => rotate/scale around
  // the layer's geometric centre (pre-M1.5 behaviour).
  pivot?: [number, number] | null
}

export interface ElementAnimation {
  element_id: string
  timeline: AnimationPrimitive[]
  easing: string
  loop: boolean
  // When true the timeline plays forward then backward (ping-pong).
  // Ideal for oscillating motions — combine with loop=true.
  yoyo?: boolean
  duration_ms: number
}

export type AnimationDSL = ElementAnimation[]

export type AnimatorTarget = Element | object
export type LayerRefs = Record<string, AnimatorTarget | null | undefined>

export class AnimatorError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AnimatorError'
  }
}

export class UnknownPrimitiveError extends AnimatorError {
  primitiveType: string
  constructor(primitiveType: string) {
    super(`Unknown animation primitive: ${primitiveType}`)
    this.name = 'UnknownPrimitiveError'
    this.primitiveType = primitiveType
  }
}

export class MissingElementError extends AnimatorError {
  elementId: string
  constructor(elementId: string) {
    super(`No layer ref registered for element_id: ${elementId}`)
    this.name = 'MissingElementError'
    this.elementId = elementId
  }
}

const PRIMITIVE_TYPES: readonly PrimitiveType[] = [
  'translate',
  'rotate',
  'scale',
  'opacity',
  'path-follow',
]

function isPrimitiveType(value: string): value is PrimitiveType {
  return (PRIMITIVE_TYPES as readonly string[]).includes(value)
}

/**
 * Convert a pivot in image-space pixel coordinates into a CSS / GSAP
 * `transform-origin` string expressed as percentages of the image size.
 *
 * Why percentages: layer `<img>` elements are rendered with
 * `object-fit: contain` inside a box whose aspect ratio matches the image,
 * so the rendered pixel size depends on layout. Percentages track the
 * image content correctly regardless of how the browser scales it.
 *
 * Returns `null` when the caller didn't supply a pivot or when image
 * dimensions are missing — the caller should then omit `transformOrigin`
 * so GSAP falls back to the default layer centre.
 */
function resolveTransformOrigin(
  step: AnimationPrimitive,
  imageWidth: number | undefined,
  imageHeight: number | undefined,
): string | null {
  if (!step.pivot) return null
  if (!imageWidth || !imageHeight) return null
  const [px, py] = step.pivot
  const xPct = (px / imageWidth) * 100
  const yPct = (py / imageHeight) * 100
  return `${xPct}% ${yPct}%`
}

function applyPrimitive(
  subTimeline: gsap.core.Timeline,
  target: AnimatorTarget,
  step: AnimationPrimitive,
  fallbackDurationSeconds: number,
  fallbackEase: string,
  imageWidth: number | undefined,
  imageHeight: number | undefined,
): void {
  if (!isPrimitiveType(step.type)) {
    throw new UnknownPrimitiveError(step.type as string)
  }

  const durationSeconds =
    step.duration_ms != null ? step.duration_ms / 1000 : fallbackDurationSeconds
  const ease = step.easing ?? fallbackEase

  // Pivots only make sense for rotate / scale; omit for translate /
  // opacity / path-follow to keep GSAP's tween vars clean.
  const pivotOrigin =
    step.type === 'rotate' || step.type === 'scale'
      ? resolveTransformOrigin(step, imageWidth, imageHeight)
      : null

  switch (step.type) {
    case 'translate':
      subTimeline.to(target, {
        x: step.dx ?? 0,
        y: step.dy ?? 0,
        duration: durationSeconds,
        ease,
      })
      return
    case 'rotate':
      subTimeline.to(target, {
        rotation: step.angle ?? 0,
        duration: durationSeconds,
        ease,
        // Only include transformOrigin when we actually resolved a pivot;
        // otherwise GSAP keeps whatever origin was active (default 50% 50%).
        ...(pivotOrigin != null ? { transformOrigin: pivotOrigin } : {}),
      })
      return
    case 'scale':
      subTimeline.to(target, {
        scale: step.scale ?? 1,
        duration: durationSeconds,
        ease,
        ...(pivotOrigin != null ? { transformOrigin: pivotOrigin } : {}),
      })
      return
    case 'opacity':
      subTimeline.to(target, {
        opacity: step.opacity ?? 1,
        duration: durationSeconds,
        ease,
      })
      return
    case 'path-follow': {
      const path = step.path ?? []
      if (path.length === 0) {
        return
      }
      const perSegmentDuration = durationSeconds / path.length
      for (const [x, y] of path) {
        subTimeline.to(target, {
          x,
          y,
          duration: perSegmentDuration,
          ease,
        })
      }
      return
    }
  }
}

export interface BuildTimelineOptions {
  paused?: boolean
  // Source image intrinsic dimensions. Supplied so pivots expressed in
  // image-space pixel coordinates can be mapped to percentage-based CSS
  // transform-origin values. Optional: when omitted, any pivot on a step
  // is ignored and rotation / scale falls back to the layer centre.
  imageWidth?: number
  imageHeight?: number
}

export function buildTimeline(
  dsl: AnimationDSL,
  layerRefs: LayerRefs,
  options: BuildTimelineOptions = {},
): gsap.core.Timeline {
  const root = gsap.timeline({ paused: options.paused ?? true })

  for (const elementAnimation of dsl) {
    const target = layerRefs[elementAnimation.element_id]
    if (target == null) {
      throw new MissingElementError(elementAnimation.element_id)
    }

    const sub = gsap.timeline({
      repeat: elementAnimation.loop ? -1 : 0,
      yoyo: elementAnimation.yoyo ?? false,
    })

    const fallbackDurationSeconds = elementAnimation.duration_ms / 1000
    const fallbackEase = elementAnimation.easing

    for (const step of elementAnimation.timeline) {
      applyPrimitive(
        sub,
        target,
        step,
        fallbackDurationSeconds,
        fallbackEase,
        options.imageWidth,
        options.imageHeight,
      )
    }

    root.add(sub, 0)
  }

  return root
}
