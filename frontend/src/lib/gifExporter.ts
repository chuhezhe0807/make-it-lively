import gsap from 'gsap'
import { GIFEncoder, applyPalette, quantize } from 'gifenc'
import type { Element as ApiElement } from './api'

export interface ExportGifOptions {
  fps?: number
  minDurationSeconds?: number
  durationSeconds?: number
  onProgress?: (fraction: number) => void
}

export interface ExportGifInput {
  timeline: GSAPTimeline
  intrinsicWidth: number
  intrinsicHeight: number
  renderedWidth: number
  renderedHeight: number
  backgroundUrl: string | null
  elements: ApiElement[]
  layerUrls: Record<string, string>
  layerRefs: Record<string, HTMLImageElement>
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`Failed to load image: ${url}`))
    img.src = url
  })
}

function readNumericProp(target: Element, prop: string, fallback: number): number {
  const value = gsap.getProperty(target, prop)
  const num = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(num) ? num : fallback
}

export async function exportGif(
  input: ExportGifInput,
  options: ExportGifOptions = {},
): Promise<Blob> {
  const fps = options.fps ?? 15
  const minDuration = options.minDurationSeconds ?? 2
  const timelineDuration = input.timeline.duration()
  const explicit = options.durationSeconds
  const duration =
    explicit != null && explicit > 0
      ? explicit
      : Math.max(minDuration, timelineDuration > 0 ? timelineDuration : minDuration)

  const frameDelayMs = Math.round(1000 / fps)
  const frameCount = Math.max(1, Math.round(fps * duration))

  const { intrinsicWidth, intrinsicHeight, renderedWidth, renderedHeight } = input
  if (intrinsicWidth <= 0 || intrinsicHeight <= 0) {
    throw new Error('Canvas dimensions are not known yet')
  }
  if (renderedWidth <= 0 || renderedHeight <= 0) {
    throw new Error('Rendered canvas size must be positive')
  }
  const scaleX = intrinsicWidth / renderedWidth
  const scaleY = intrinsicHeight / renderedHeight

  const ordered = [...input.elements].sort((a, b) => a.z_order - b.z_order)

  const bgImage = input.backgroundUrl ? await loadImage(input.backgroundUrl) : null
  const layerImages: Record<string, HTMLImageElement> = {}
  await Promise.all(
    ordered.map(async (el) => {
      const url = input.layerUrls[el.id]
      if (url) {
        layerImages[el.id] = await loadImage(url)
      }
    }),
  )

  const canvas = document.createElement('canvas')
  canvas.width = intrinsicWidth
  canvas.height = intrinsicHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    throw new Error('Failed to get 2D context for GIF canvas')
  }

  const gif = GIFEncoder()
  input.timeline.pause()

  try {
    for (let i = 0; i < frameCount; i++) {
      const t = frameCount === 1 ? 0 : (i / frameCount) * duration
      input.timeline.pause(t)

      ctx.clearRect(0, 0, intrinsicWidth, intrinsicHeight)
      ctx.fillStyle = '#000000'
      ctx.fillRect(0, 0, intrinsicWidth, intrinsicHeight)

      if (bgImage) {
        ctx.drawImage(bgImage, 0, 0, intrinsicWidth, intrinsicHeight)
      }

      for (const el of ordered) {
        const img = layerImages[el.id]
        if (!img) continue
        const target = input.layerRefs[el.id]
        let x = 0
        let y = 0
        let rotation = 0
        let scaleVal = 1
        let opacity = 1
        if (target) {
          x = readNumericProp(target, 'x', 0)
          y = readNumericProp(target, 'y', 0)
          rotation = readNumericProp(target, 'rotation', 0)
          scaleVal = readNumericProp(target, 'scale', 1)
          opacity = readNumericProp(target, 'opacity', 1)
        }
        ctx.save()
        ctx.globalAlpha = Math.max(0, Math.min(1, opacity))
        ctx.translate(
          intrinsicWidth / 2 + x * scaleX,
          intrinsicHeight / 2 + y * scaleY,
        )
        ctx.rotate((rotation * Math.PI) / 180)
        ctx.scale(scaleVal, scaleVal)
        ctx.drawImage(
          img,
          -intrinsicWidth / 2,
          -intrinsicHeight / 2,
          intrinsicWidth,
          intrinsicHeight,
        )
        ctx.restore()
      }

      const imageData = ctx.getImageData(0, 0, intrinsicWidth, intrinsicHeight)
      const palette = quantize(imageData.data, 256, { format: 'rgb444' })
      const index = applyPalette(imageData.data, palette, 'rgb444')
      gif.writeFrame(index, intrinsicWidth, intrinsicHeight, {
        palette,
        delay: frameDelayMs,
        first: i === 0,
        repeat: 0,
      })

      if (options.onProgress) {
        options.onProgress((i + 1) / frameCount)
      }

      await new Promise<void>((resolve) => setTimeout(resolve, 0))
    }
  } finally {
    input.timeline.pause(0)
  }

  gif.finish()
  const bytes = gif.bytes()
  const buffer = new Uint8Array(bytes.length)
  buffer.set(bytes)
  return new Blob([buffer], { type: 'image/gif' })
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
