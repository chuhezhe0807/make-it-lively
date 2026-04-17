# Make It Lively — Backend

FastAPI service powering image perception, segmentation, inpainting, and animation planning.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Setup

```bash
cd backend
uv sync --extra dev
```

Or with pip:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uv run uvicorn app.main:app --reload
```

The server starts on http://localhost:8000. CORS is configured to allow the Vite
frontend running on http://localhost:5173.

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Image upload (PNG/JPG/WebP, ≤10MB):

```bash
curl -F "file=@./sample.png" http://localhost:8000/api/upload
# {"image_id":"…","width":800,"height":600}
```

Uploaded files are written to `backend/storage/images/{image_id}.{ext}` (gitignored).

VLM perception (requires `ANTHROPIC_API_KEY`):

```bash
curl -X POST http://localhost:8000/api/perception \
  -H "Content-Type: application/json" \
  -d '{"image_id":"…"}'
# {"image_id":"…","elements":[{"id":"cat","label":"Orange cat","bbox":[…],"z_order":2}]}
```

Perception results are cached at `backend/storage/perception/{image_id}.json`; repeat
calls skip the VLM.

SAM2 segmentation (requires `REPLICATE_API_TOKEN`):

```bash
curl -X POST http://localhost:8000/api/segment \
  -H "Content-Type: application/json" \
  -d '{"image_id":"…","elements":[{"id":"cat","label":"Orange cat","bbox":[10,20,30,40],"z_order":2}]}'
# {"image_id":"…","layers":[{"element_id":"cat","url":"/storage/layers/…/cat.png"}]}
```

Transparent layers are written to `backend/storage/layers/{image_id}/{element_id}.png` and
served read-only at `/storage/layers/...`.

Background inpainting (requires `REPLICATE_API_TOKEN`):

```bash
curl -X POST http://localhost:8000/api/inpaint \
  -H "Content-Type: application/json" \
  -d '{"image_id":"…","masks":[{"bbox":[10,20,30,40]}]}'
# {"image_id":"…","background_url":"/storage/layers/…/background.png"}
```

The filled background is written to
`backend/storage/layers/{image_id}/background.png`. Empty `masks` short-circuits
the Replicate call and copies the original image through.

Animation planning (requires `ANTHROPIC_API_KEY`):

```bash
curl -X POST http://localhost:8000/api/plan-animation \
  -H "Content-Type: application/json" \
  -d '{"image_id":"…","elements":[{"id":"cat","label":"Orange cat","bbox":[10,20,30,40],"z_order":2}],"prompt":"make the cat bounce"}'
# {"image_id":"…","plan":[{"element_id":"cat","timeline":[…],"easing":"power1.inOut","loop":true,"duration_ms":1200}]}
```

The planner returns a GSAP-compatible DSL with five primitives (`translate`,
`rotate`, `scale`, `opacity`, `path-follow`). The schema lives in
`backend/app/schemas/animation.py`. Responses that reference unknown
`element_id`s are rejected as 502.

## Quality checks

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

## Environment variables

Required keys:

- **Anthropic** — used by `/api/perception` and `/api/plan-animation`. Set
  **one** of:
  - `ANTHROPIC_API_KEY` — sent as `x-api-key` (native Anthropic).
  - `ANTHROPIC_AUTH_TOKEN` — sent as `Authorization: Bearer …` (common for
    OneAPI / reverse-proxy gateways).
- **Replicate** — used by `/api/segment` and `/api/inpaint`:
  - `REPLICATE_API_TOKEN` — required unless the fallback below is enabled.

Optional:

- `ANTHROPIC_BASE_URL` — point the Anthropic SDK at a proxy/gateway.
- `USE_REPLICATE_FALLBACK` — force local Pillow fallbacks even when
  `REPLICATE_API_TOKEN` is set. Truthy values: `1` / `true` / `yes`.

### Replicate fallback mode

If `REPLICATE_API_TOKEN` is missing (or `USE_REPLICATE_FALLBACK` is truthy),
the backend uses local Pillow implementations instead of Replicate:

- `/api/segment`: rectangular bbox crop on a transparent canvas (no SAM2
  silhouette; edges are straight rectangles).
- `/api/inpaint`: feathered Gaussian-blur composite over the masked regions
  (not a true inpaint; hides the hole well enough to sit under the foreground).

This keeps the whole pipeline runnable without a Replicate account.

### How to supply the values

1. **`backend/.env` file (recommended for local dev)**
   Copy the template and fill in your keys. `app/config.py` loads the file at
   startup via `python-dotenv`. `backend/.env` is gitignored.
   ```bash
   cp backend/.env.example backend/.env
   # then edit backend/.env
   ```

2. **Export in the shell** — real environment variables take precedence over
   the `.env` file (useful in CI or when you want to override locally):
   ```bash
   export ANTHROPIC_AUTH_TOKEN=...
   export ANTHROPIC_BASE_URL=https://your-proxy.example.com/
   export USE_REPLICATE_FALLBACK=true
   ```
