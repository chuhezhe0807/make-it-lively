import { describe, expect, test } from 'vitest'
import {
  buildTimeline,
  MissingElementError,
  UnknownPrimitiveError,
  type AnimationPrimitive,
  type ElementAnimation,
} from './animator'

function makeTarget(): Record<string, unknown> {
  return {}
}

function elementAnimation(
  primitive: AnimationPrimitive,
  overrides: Partial<ElementAnimation> = {},
): ElementAnimation {
  return {
    element_id: 'el',
    timeline: [primitive],
    easing: 'power1.inOut',
    loop: false,
    duration_ms: 1000,
    ...overrides,
  }
}

function getFirstTween(timeline: GSAPTimeline): GSAPTween {
  const tweens = timeline.getChildren(true, true, false)
  expect(tweens.length).toBeGreaterThan(0)
  return tweens[0] as GSAPTween
}

function getSubTimeline(timeline: GSAPTimeline): GSAPTimeline {
  const nested = timeline.getChildren(false, false, true)
  expect(nested.length).toBe(1)
  return nested[0] as GSAPTimeline
}

describe('buildTimeline — translate primitive', () => {
  test('translate emits x/y tween with correct duration', () => {
    const target = makeTarget()
    const tl = buildTimeline(
      [
        elementAnimation({ type: 'translate', dx: 120, dy: -40 }, { duration_ms: 500 }),
      ],
      { el: target },
    )
    expect(tl.duration()).toBeCloseTo(0.5)
    const tween = getFirstTween(tl)
    expect(tween.vars.x).toBe(120)
    expect(tween.vars.y).toBe(-40)
    expect(tween.vars.ease).toBe('power1.inOut')
  })

  test('translate falls back to 0 when dx/dy omitted', () => {
    const tl = buildTimeline(
      [elementAnimation({ type: 'translate' })],
      { el: makeTarget() },
    )
    const tween = getFirstTween(tl)
    expect(tween.vars.x).toBe(0)
    expect(tween.vars.y).toBe(0)
  })
})

describe('buildTimeline — rotate primitive', () => {
  test('rotate emits rotation tween with step-level easing override', () => {
    const tl = buildTimeline(
      [
        elementAnimation({
          type: 'rotate',
          angle: 90,
          easing: 'bounce.out',
          duration_ms: 750,
        }),
      ],
      { el: makeTarget() },
    )
    expect(tl.duration()).toBeCloseTo(0.75)
    const tween = getFirstTween(tl)
    expect(tween.vars.rotation).toBe(90)
    expect(tween.vars.ease).toBe('bounce.out')
  })
})

describe('buildTimeline — scale primitive', () => {
  test('scale emits scale tween', () => {
    const tl = buildTimeline(
      [elementAnimation({ type: 'scale', scale: 1.5 })],
      { el: makeTarget() },
    )
    expect(tl.duration()).toBeCloseTo(1)
    const tween = getFirstTween(tl)
    expect(tween.vars.scale).toBe(1.5)
  })
})

describe('buildTimeline — opacity primitive', () => {
  test('opacity emits opacity tween', () => {
    const tl = buildTimeline(
      [elementAnimation({ type: 'opacity', opacity: 0 }, { duration_ms: 250 })],
      { el: makeTarget() },
    )
    expect(tl.duration()).toBeCloseTo(0.25)
    const tween = getFirstTween(tl)
    expect(tween.vars.opacity).toBe(0)
  })
})

describe('buildTimeline — path-follow primitive', () => {
  test('path-follow emits one tween per waypoint summing to full duration', () => {
    const path: Array<[number, number]> = [
      [0, 0],
      [50, 10],
      [100, 0],
    ]
    const tl = buildTimeline(
      [
        elementAnimation(
          { type: 'path-follow', path, duration_ms: 2000, easing: 'sine.out' },
          { duration_ms: 999 /* overridden by step */ },
        ),
      ],
      { el: makeTarget() },
    )
    expect(tl.duration()).toBeCloseTo(2)
    const tweens = tl.getChildren(true, true, false)
    expect(tweens.length).toBe(3)
    const [first, second, third] = tweens as GSAPTween[]
    expect(first.vars.x).toBe(0)
    expect(first.vars.y).toBe(0)
    expect(first.vars.ease).toBe('sine.out')
    expect(second.vars.x).toBe(50)
    expect(second.vars.y).toBe(10)
    expect(third.vars.x).toBe(100)
    expect(third.vars.y).toBe(0)
  })
})

describe('buildTimeline — loop support', () => {
  test('loop=true marks sub-timeline repeat as -1', () => {
    const tl = buildTimeline(
      [
        elementAnimation(
          { type: 'translate', dx: 10, dy: 0 },
          { loop: true },
        ),
      ],
      { el: makeTarget() },
    )
    const sub = getSubTimeline(tl)
    expect(sub.repeat()).toBe(-1)
  })

  test('loop=false leaves repeat at 0', () => {
    const tl = buildTimeline(
      [elementAnimation({ type: 'translate', dx: 10, dy: 0 })],
      { el: makeTarget() },
    )
    const sub = getSubTimeline(tl)
    expect(sub.repeat()).toBe(0)
  })
})

describe('buildTimeline — multi-element timelines run concurrently', () => {
  test('sub-timelines added at 0 so total duration = max', () => {
    const tl = buildTimeline(
      [
        elementAnimation(
          { type: 'translate', dx: 10, dy: 0 },
          { element_id: 'a', duration_ms: 500 },
        ),
        elementAnimation(
          { type: 'translate', dx: 20, dy: 0 },
          { element_id: 'b', duration_ms: 1500 },
        ),
      ],
      { a: makeTarget(), b: makeTarget() },
    )
    expect(tl.duration()).toBeCloseTo(1.5)
    const subs = tl.getChildren(false, false, true)
    expect(subs.length).toBe(2)
  })
})

describe('buildTimeline — error handling', () => {
  test('missing element_id throws MissingElementError', () => {
    expect(() =>
      buildTimeline(
        [elementAnimation({ type: 'translate', dx: 0, dy: 0 }, { element_id: 'ghost' })],
        {},
      ),
    ).toThrow(MissingElementError)
  })

  test('null layer ref throws MissingElementError', () => {
    expect(() =>
      buildTimeline(
        [elementAnimation({ type: 'translate', dx: 0, dy: 0 })],
        { el: null },
      ),
    ).toThrow(MissingElementError)
  })

  test('unknown primitive throws UnknownPrimitiveError', () => {
    const bogus = { type: 'teleport' } as unknown as AnimationPrimitive
    expect(() =>
      buildTimeline([elementAnimation(bogus)], { el: makeTarget() }),
    ).toThrow(UnknownPrimitiveError)
  })
})
