import { GIFEncoder, applyPalette, quantize } from 'gifenc'
import type { Element as ApiElement } from './api'
import { renderFrame, type FrameRenderContext } from './renderFrame'

// GIF 不需要源图原始分辨率，长边限制到 800px 可大幅降低
// getImageData / quantize / applyPalette / LZW 的开销
const MAX_GIF_DIMENSION = 800

// 相邻帧颜色差异很小，每隔 N 帧才重新做一次 NeuQuant 量化，
// 中间帧复用已有 palette，省掉 ~30% 的量化开销
const PALETTE_REUSE_INTERVAL = 15

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

export async function exportGif(
  input: ExportGifInput,
  options: ExportGifOptions = {},
): Promise<Blob> {
  const fps = options.fps ?? 15
  const minDuration = options.minDurationSeconds ?? 2
  // root.duration() 会包含子 timeline 的 repeat，当 loop=true (repeat:-1)
  // 时结果是 Infinity。改为取各子 timeline 单次循环 duration 的最大值。
  const children = input.timeline.getChildren(false, false, true) as gsap.core.Timeline[]
  const timelineDuration =
    children.length > 0
      ? Math.max(...children.map((c) => c.duration()))
      : input.timeline.duration()
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

  // 将 GIF 输出尺寸限制在 MAX_GIF_DIMENSION 以内，等比缩放
  const downscale = Math.min(
    1,
    MAX_GIF_DIMENSION / Math.max(intrinsicWidth, intrinsicHeight),
  )
  const gifW = Math.round(intrinsicWidth * downscale)
  const gifH = Math.round(intrinsicHeight * downscale)

  // GSAP 动画偏移量是 CSS 像素空间，需要换算到 GIF canvas 空间
  const scaleX = gifW / renderedWidth
  const scaleY = gifH / renderedHeight

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
  canvas.width = gifW
  canvas.height = gifH
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) {
    throw new Error('Failed to get 2D context for GIF canvas')
  }

  const rc: FrameRenderContext = {
    ctx, canvasW: gifW, canvasH: gifH,
    scaleX, scaleY, renderedWidth, renderedHeight,
    bgImage, ordered, layerImages, layerRefs: input.layerRefs,
  }

  const gif = GIFEncoder()
  input.timeline.pause()

  // 缓存 palette 用于帧间复用
  let cachedPalette: number[][] | null = null

  try {
    for (let i = 0; i < frameCount; i++) {
      const t = frameCount === 1 ? 0 : (i / frameCount) * duration
      input.timeline.pause(t)

      renderFrame(rc)
      const imageData = ctx.getImageData(0, 0, gifW, gifH)

      // 每 PALETTE_REUSE_INTERVAL 帧重新量化一次，中间帧复用 palette
      const needsNewPalette = i % PALETTE_REUSE_INTERVAL === 0 || cachedPalette === null
      if (needsNewPalette) {
        cachedPalette = quantize(imageData.data, 256, { format: 'rgb444' })
      }
      const index = applyPalette(imageData.data, cachedPalette!, 'rgb444')
      gif.writeFrame(index, gifW, gifH, {
        palette: cachedPalette!,
        delay: frameDelayMs,
        first: i === 0,
        repeat: 0,
      })

      if (options.onProgress) {
        options.onProgress((i + 1) / frameCount)
      }

      // 让出主线程，避免 UI 完全冻结
      await new Promise<void>((resolve) => setTimeout(resolve, 0))
    }
  } finally {
    input.timeline.pause(0)
  }

  gif.finish()
  return new Blob([gif.bytes().slice().buffer], { type: 'image/gif' })
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
