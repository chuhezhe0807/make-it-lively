# Make It Lively — Frontend

Vite + Vue 3 + TypeScript + Tailwind CSS 4. Uses Vue Router with routes:

- `/` — home (upload)
- `/editor/:imageId` — per-image editor

## Setup

```bash
npm install
```

## Run

```bash
npm run dev       # http://localhost:5173 (strictPort)
npm run build     # vue-tsc typecheck + production build
npm run preview   # preview the production build
```

## API base URL

The backend base URL defaults to `http://localhost:8000`. Override by
setting `VITE_API_BASE_URL` before `npm run dev`/`npm run build`.

`src/lib/api.ts` exposes the fetch wrapper and typed helpers (e.g. `uploadImage`).
