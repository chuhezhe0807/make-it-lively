/**
 * 共享的逐帧 canvas 渲染逻辑，GIF 和 Video 导出器共用。
 *
 * 核心要点：GSAP 动画可能通过 pivot 设置自定义 transformOrigin，
 * 必须读取每个元素的 transformOrigin 并在仿射矩阵中正确应用，
 * 否则旋转/缩放的轴心会错位（例如猫腿绕中心转而不是绕髋关节转）。
 */
import gsap from 'gsap'
import type { Element as ApiElement } from './api'

export interface FrameRenderContext {
  ctx: CanvasRenderingContext2D
  /** canvas 像素宽度 */
  canvasW: number
  /** canvas 像素高度 */
  canvasH: number
  /** CSS 渲染宽度 → canvas 的缩放系数 */
  scaleX: number
  /** CSS 渲染高度 → canvas 的缩放系数 */
  scaleY: number
  /** CSS 渲染宽度（用于解析 transformOrigin 的 px 值） */
  renderedWidth: number
  /** CSS 渲染高度 */
  renderedHeight: number
  bgImage: HTMLImageElement | null
  ordered: ApiElement[]
  layerImages: Record<string, HTMLImageElement>
  layerRefs: Record<string, HTMLImageElement>
}

function readNumericProp(target: Element, prop: string, fallback: number): number {
  const value = gsap.getProperty(target, prop)
  const num = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(num) ? num : fallback
}

/**
 * 解析 CSS transformOrigin 的单个分量（"386.5px" 或 "50%"），
 * 返回 0-1 之间的比例值。
 */
function parseOriginPart(s: string, cssSize: number): number {
  const trimmed = s.trim()
  if (trimmed.endsWith('%')) {
    return parseFloat(trimmed) / 100
  }
  // px 值：除以 CSS 渲染尺寸得到比例
  return parseFloat(trimmed) / cssSize
}

/**
 * 读取 GSAP 元素上当前生效的 transformOrigin，
 * 返回 canvas 坐标系下的原点 (ox, oy)。
 */
function getTransformOrigin(
  target: Element,
  renderedWidth: number,
  renderedHeight: number,
  canvasW: number,
  canvasH: number,
): [number, number] {
  const raw = String(gsap.getProperty(target, 'transformOrigin') ?? '50% 50%')
  const parts = raw.trim().split(/\s+/)
  // 默认 50% 50%（CSS 默认值）
  const fracX = parseOriginPart(parts[0] ?? '50%', renderedWidth)
  const fracY = parseOriginPart(parts[1] ?? '50%', renderedHeight)
  return [fracX * canvasW, fracY * canvasH]
}

/**
 * 将当前 GSAP timeline 状态渲染到 canvas 上。
 * 正确处理 transformOrigin，匹配 CSS 渲染效果。
 */
export function renderFrame(rc: FrameRenderContext): void {
  const { ctx, canvasW, canvasH, scaleX, scaleY, renderedWidth, renderedHeight } = rc

  // 重置变换 + 清屏
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.globalAlpha = 1
  ctx.clearRect(0, 0, canvasW, canvasH)
  ctx.fillStyle = '#000000'
  ctx.fillRect(0, 0, canvasW, canvasH)

  if (rc.bgImage) {
    ctx.drawImage(rc.bgImage, 0, 0, canvasW, canvasH)
  }

  for (const el of rc.ordered) {
    const img = rc.layerImages[el.id]
    if (!img) continue
    const target = rc.layerRefs[el.id]
    let x = 0
    let y = 0
    let rotation = 0
    let scaleVal = 1
    let opacity = 1
    // 默认 transformOrigin：canvas 中心
    let ox = canvasW / 2
    let oy = canvasH / 2

    if (target) {
      x = readNumericProp(target, 'x', 0)
      y = readNumericProp(target, 'y', 0)
      rotation = readNumericProp(target, 'rotation', 0)
      scaleVal = readNumericProp(target, 'scale', 1)
      opacity = readNumericProp(target, 'opacity', 1)
      ;[ox, oy] = getTransformOrigin(target, renderedWidth, renderedHeight, canvasW, canvasH)
    }

    // CSS 变换矩阵：T(ox,oy) · translate(dx,dy) · rotate(a) · scale(s) · T(-ox,-oy)
    // 其中 dx = x * scaleX, dy = y * scaleY（GSAP 偏移从 CSS px 换算到 canvas px）
    const rad = (rotation * Math.PI) / 180
    const cos = Math.cos(rad) * scaleVal
    const sin = Math.sin(rad) * scaleVal
    const dx = x * scaleX
    const dy = y * scaleY
    const tx = ox + dx - cos * ox + sin * oy
    const ty = oy + dy - sin * ox - cos * oy

    ctx.setTransform(cos, sin, -sin, cos, tx, ty)
    ctx.globalAlpha = Math.max(0, Math.min(1, opacity))
    // 图层图片与原图同尺寸，铺满整个 canvas（透明区域自动不可见）
    ctx.drawImage(img, 0, 0, canvasW, canvasH)
  }

  // 重置变换，方便后续 getImageData / VideoFrame
  ctx.setTransform(1, 0, 0, 1, 0, 0)
}
