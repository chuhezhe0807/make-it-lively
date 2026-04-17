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
}

export interface ElementAnimation {
  element_id: string
  timeline: AnimationPrimitive[]
  easing: string
  loop: boolean
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

function applyPrimitive(
  subTimeline: gsap.core.Timeline,
  target: AnimatorTarget,
  step: AnimationPrimitive,
  fallbackDurationSeconds: number,
  fallbackEase: string,
): void {
  if (!isPrimitiveType(step.type)) {
    throw new UnknownPrimitiveError(step.type as string)
  }

  const durationSeconds =
    step.duration_ms != null ? step.duration_ms / 1000 : fallbackDurationSeconds
  const ease = step.easing ?? fallbackEase

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
      })
      return
    case 'scale':
      subTimeline.to(target, {
        scale: step.scale ?? 1,
        duration: durationSeconds,
        ease,
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
    })

    const fallbackDurationSeconds = elementAnimation.duration_ms / 1000
    const fallbackEase = elementAnimation.easing

    for (const step of elementAnimation.timeline) {
      applyPrimitive(sub, target, step, fallbackDurationSeconds, fallbackEase)
    }

    root.add(sub, 0)
  }

  return root
}
