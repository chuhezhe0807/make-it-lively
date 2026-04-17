declare module 'gifenc' {
  export type PaletteFormat = 'rgb565' | 'rgb444' | 'rgba4444'

  export interface GifEncoderInstance {
    writeHeader(): void
    writeFrame(
      index: Uint8Array,
      width: number,
      height: number,
      opts?: {
        palette?: number[][]
        delay?: number
        transparent?: boolean
        transparentIndex?: number
        first?: boolean
        dispose?: number
        repeat?: number
      },
    ): void
    finish(): void
    bytes(): Uint8Array
    bytesView(): Uint8Array
    reset(): void
  }

  export function GIFEncoder(opts?: {
    initialCapacity?: number
    auto?: boolean
  }): GifEncoderInstance

  export function quantize(
    rgba: Uint8Array | Uint8ClampedArray,
    maxColors?: number,
    opts?: {
      format?: PaletteFormat
      clearAlpha?: boolean
      clearAlphaThreshold?: number
      clearAlphaColor?: number
      oneBitAlpha?: boolean | number
    },
  ): number[][]

  export function applyPalette(
    rgba: Uint8Array | Uint8ClampedArray,
    palette: number[][],
    format?: PaletteFormat,
  ): Uint8Array
}
