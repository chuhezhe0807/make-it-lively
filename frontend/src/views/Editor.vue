<script setup lang="ts">
defineProps<{ imageId: string }>()
</script>

<template>
  <main class="min-h-screen bg-slate-950 text-slate-100 p-6">
    <h1 class="text-2xl font-semibold mb-4">Editor</h1>
    <p class="text-slate-400">Image ID: {{ imageId }}</p>
  </main>
</template>
