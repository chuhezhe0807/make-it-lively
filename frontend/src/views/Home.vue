<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, uploadImage } from '../lib/api'

const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

const router = useRouter()
const fileInput = ref<HTMLInputElement | null>(null)
const isDragging = ref(false)
const isUploading = ref(false)
const progress = ref(0)
const errorMessage = ref<string | null>(null)

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) {
      return 'Network error — is the backend running?'
    }
    if (err.body && typeof err.body === 'object' && 'detail' in err.body) {
      const detail = (err.body as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
    }
    return `Upload failed (${err.status})`
  }
  if (err instanceof Error) return err.message
  return 'Upload failed'
}

function validate(file: File): string | null {
  if (!ACCEPTED_TYPES.includes(file.type)) {
    return 'Unsupported format. Please upload PNG, JPEG, or WebP.'
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return 'File is too large. Maximum size is 10MB.'
  }
  return null
}

async function handleFile(file: File): Promise<void> {
  errorMessage.value = null
  const invalid = validate(file)
  if (invalid) {
    errorMessage.value = invalid
    return
  }
  isUploading.value = true
  progress.value = 0
  try {
    const response = await uploadImage(file, {
      onProgress: (fraction) => {
        progress.value = fraction
      },
    })
    await router.push({ name: 'editor', params: { imageId: response.image_id } })
  } catch (err) {
    errorMessage.value = formatError(err)
  } finally {
    isUploading.value = false
  }
}

function onDrop(event: DragEvent): void {
  event.preventDefault()
  isDragging.value = false
  if (isUploading.value) return
  const file = event.dataTransfer?.files?.[0]
  if (file) void handleFile(file)
}

function onDragOver(event: DragEvent): void {
  event.preventDefault()
  if (!isUploading.value) isDragging.value = true
}

function onDragLeave(event: DragEvent): void {
  event.preventDefault()
  isDragging.value = false
}

function onPickFile(): void {
  if (isUploading.value) return
  fileInput.value?.click()
}

function onInputChange(event: Event): void {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) void handleFile(file)
  target.value = ''
}
</script>

<template>
  <main class="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100 p-6">
    <div class="w-full max-w-xl">
      <h1 class="text-4xl font-bold mb-2 text-center">Make It Lively</h1>
      <p class="text-slate-400 mb-8 text-center">Upload an image to animate its elements.</p>

      <div
        data-testid="dropzone"
        class="border-2 border-dashed rounded-2xl p-10 text-center transition-colors cursor-pointer select-none"
        :class="[
          isDragging ? 'border-indigo-400 bg-indigo-500/10' : 'border-slate-700 bg-slate-900/50',
          isUploading ? 'pointer-events-none opacity-70' : 'hover:border-slate-500',
        ]"
        role="button"
        tabindex="0"
        @click="onPickFile"
        @keydown.enter.prevent="onPickFile"
        @keydown.space.prevent="onPickFile"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
      >
        <p class="text-lg font-medium mb-2">Drag & drop an image here</p>
        <p class="text-sm text-slate-400 mb-4">or click to browse</p>
        <p class="text-xs text-slate-500">PNG, JPEG, or WebP · up to 10MB</p>

        <input
          ref="fileInput"
          type="file"
          class="hidden"
          accept="image/png,image/jpeg,image/webp"
          @change="onInputChange"
        />
      </div>

      <div v-if="isUploading" class="mt-6" data-testid="upload-progress">
        <div class="flex justify-between text-xs text-slate-400 mb-1">
          <span>Uploading…</span>
          <span>{{ Math.round(progress * 100) }}%</span>
        </div>
        <div class="h-2 w-full rounded-full bg-slate-800 overflow-hidden">
          <div
            class="h-full bg-indigo-500 transition-all"
            :style="{ width: `${Math.round(progress * 100)}%` }"
          ></div>
        </div>
      </div>

      <div
        v-if="errorMessage"
        class="mt-6 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200"
        role="alert"
        data-testid="upload-error"
      >
        {{ errorMessage }}
      </div>
    </div>
  </main>
</template>
