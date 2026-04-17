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

export const uploadImage = (file: File): Promise<UploadResponse> => {
  const form = new FormData()
  form.append('file', file)
  return api.postForm<UploadResponse>('/api/upload', form)
}
