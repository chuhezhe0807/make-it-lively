import { Muxer, ArrayBufferTarget } from 'mp4-muxer'
import type { Element as ApiElement } from './api'
import { renderFrame, type FrameRenderContext } from './renderFrame'

export interface ExportVideoOptions {
  fps?: number
  minDurationSeconds?: number
  durationSeconds?: number
  /** 视频码率（bps），默认 4Mbps */
  bitrate?: number
  onProgress?: (fraction: number) => void
}

export interface ExportVideoInput {
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

/** H.264 要求宽高为偶数 */
function roundToEven(n: number): number {
  const r = Math.round(n)
  return r % 2 === 0 ? r : r + 1
}

export async function exportVideo(
  input: ExportVideoInput,
  options: ExportVideoOptions = {},
): Promise<Blob> {
  const fps = options.fps ?? 30
  const bitrate = options.bitrate ?? 4_000_000
  const minDuration = options.minDurationSeconds ?? 2

  // 与 gifExporter 相同的 duration 计算：取子 timeline 单次循环最大值
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

  const frameCount = Math.max(1, Math.round(fps * duration))
  const frameDurationUs = Math.round(1_000_000 / fps) // 微秒

  const { intrinsicWidth, intrinsicHeight, renderedWidth, renderedHeight } = input
  if (intrinsicWidth <= 0 || intrinsicHeight <= 0) {
    throw new Error('Canvas dimensions are not known yet')
  }
  if (renderedWidth <= 0 || renderedHeight <= 0) {
    throw new Error('Rendered canvas size must be positive')
  }

  // H.264 要求宽高为偶数
  const videoW = roundToEven(intrinsicWidth)
  const videoH = roundToEven(intrinsicHeight)

  // GSAP 动画偏移量从 CSS 像素空间换算到 canvas 空间
  const scaleX = videoW / renderedWidth
  const scaleY = videoH / renderedHeight

  const ordered = [...input.elements].sort((a, b) => a.z_order - b.z_order)

  // 预加载所有图层图片
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

  // 创建离屏 canvas
  const canvas = document.createElement('canvas')
  canvas.width = videoW
  canvas.height = videoH
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    throw new Error('Failed to get 2D context for video canvas')
  }

  const rc: FrameRenderContext = {
    ctx, canvasW: videoW, canvasH: videoH,
    scaleX, scaleY, renderedWidth, renderedHeight,
    bgImage, ordered, layerImages, layerRefs: input.layerRefs,
  }

  // 配置 mp4-muxer
  const muxer = new Muxer({
    target: new ArrayBufferTarget(),
    video: {
      codec: 'avc',
      width: videoW,
      height: videoH,
      frameRate: fps,
    },
    // 先积攒在内存，finalize 时再生成完整 MP4（支持 fast start）
    fastStart: 'in-memory',
  })

  // 配置 VideoEncoder
  const encoder = new VideoEncoder({
    output: (chunk, meta) => {
      muxer.addVideoChunk(chunk, meta ?? undefined)
    },
    error: (e) => {
      throw new Error(`VideoEncoder error: ${e.message}`)
    },
  })

  encoder.configure({
    codec: 'avc1.640028', // H.264 High Profile, Level 4.0 (支持到 2048x1024)
    width: videoW,
    height: videoH,
    bitrate,
    framerate: fps,
  })

  input.timeline.pause()

  try {
    for (let i = 0; i < frameCount; i++) {
      const t = frameCount === 1 ? 0 : (i / frameCount) * duration
      input.timeline.pause(t)

      renderFrame(rc)
      const frame = new VideoFrame(canvas, {
        timestamp: i * frameDurationUs,
        duration: frameDurationUs,
      })
      // 每隔一定帧数插入关键帧，方便播放器 seek
      encoder.encode(frame, { keyFrame: i % (fps * 2) === 0 })
      frame.close()

      if (options.onProgress) {
        options.onProgress((i + 1) / frameCount)
      }

      // 如果编码器队列积压过多，等待一下避免内存暴涨
      while (encoder.encodeQueueSize > 10) {
        await new Promise<void>((resolve) => setTimeout(resolve, 1))
      }

      // 让出主线程
      if (i % 10 === 0) {
        await new Promise<void>((resolve) => setTimeout(resolve, 0))
      }
    }

    // 等待编码器完成所有帧
    await encoder.flush()
  } finally {
    encoder.close()
    input.timeline.pause(0)
  }

  // 生成最终 MP4 文件
  muxer.finalize()
  const buffer = muxer.target.buffer
  return new Blob([buffer], { type: 'video/mp4' })
}
