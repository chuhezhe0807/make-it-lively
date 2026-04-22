import type { AnimationDSL } from './animator'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export class ApiError extends Error {
  status: number
  statusText: string
  body: unknown

  constructor(status: number, statusText: string, body: unknown) {
    super(`API ${status} ${statusText}`)
    this.status = status
    this.statusText = statusText
    this.body = body
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init)
  const text = await res.text()
  const body: unknown = text ? (JSON.parse(text) as unknown) : null
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText, body)
  }
  return body as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  postJson: <T>(path: string, data: JsonValue) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  postForm: <T>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form }),
}

export interface UploadResponse {
  image_id: string
  width: number
  height: number
}

export interface UploadOptions {
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

export interface Element {
  id: string
  label: string
  bbox: [number, number, number, number]
  z_order: number
  // Optional articulated-hierarchy fields — set by the VLM when it
  // decomposes an object (e.g. robot) into movable sub-parts.
  parent_id?: string | null
  // [x, y] pixel coords (image-space) — natural rotation / scale anchor.
  pivot?: [number, number] | null
}

export interface PerceptionResponse {
  image_id: string
  elements: Element[]
}

export interface Layer {
  element_id: string
  url: string
  contour?: Array<[number, number]> | null
  centroid?: [number, number] | null
  // Tight bbox derived from the mask contour — more accurate than the VLM estimate.
  refined_bbox?: [number, number, number, number] | null
}

export interface SegmentResponse {
  image_id: string
  layers: Layer[]
}

export interface InpaintResponse {
  image_id: string
  background_url: string
}

export interface AnimationPlanResponse {
  image_id: string
  plan: AnimationDSL
}

export const perceiveElements = (imageId: string): Promise<PerceptionResponse> =>
  api.postJson<PerceptionResponse>('/api/perception', { image_id: imageId })

export const segmentElements = (
  imageId: string,
  elements: Element[],
): Promise<SegmentResponse> =>
  api.postJson<SegmentResponse>('/api/segment', {
    image_id: imageId,
    elements: elements as unknown as JsonValue,
  })

export const inpaintBackground = (
  imageId: string,
  masks: Array<{
    bbox: [number, number, number, number]
    contour?: Array<[number, number]> | null
  }>,
): Promise<InpaintResponse> =>
  api.postJson<InpaintResponse>('/api/inpaint', {
    image_id: imageId,
    masks: masks as unknown as JsonValue,
  })

export const planAnimation = (
  imageId: string,
  elements: Element[],
  prompt: string,
): Promise<AnimationPlanResponse> =>
  api.postJson<AnimationPlanResponse>('/api/plan-animation', {
    image_id: imageId,
    elements: elements as unknown as JsonValue,
    prompt,
  })

export const uploadImage = (
  file: File,
  options: UploadOptions = {},
): Promise<UploadResponse> => {
  const form = new FormData()
  form.append('file', file)

  return new Promise<UploadResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE_URL}/api/upload`)

    if (options.onProgress) {
      const onProgress = options.onProgress
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(event.loaded / event.total)
        }
      }
    }

    xhr.onload = () => {
      const text = xhr.responseText
      let body: unknown = null
      if (text) {
        try {
          body = JSON.parse(text) as unknown
        } catch {
          body = text
        }
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as UploadResponse)
      } else {
        reject(new ApiError(xhr.status, xhr.statusText, body))
      }
    }

    xhr.onerror = () => {
      reject(new ApiError(0, 'Network Error', null))
    }

    xhr.onabort = () => {
      reject(new ApiError(0, 'Aborted', null))
    }

    if (options.signal) {
      if (options.signal.aborted) {
        xhr.abort()
        return
      }
      options.signal.addEventListener('abort', () => xhr.abort(), { once: true })
    }

    xhr.send(form)
  })
}
