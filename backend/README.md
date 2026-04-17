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

## Quality checks

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

## Environment variables

- `ANTHROPIC_API_KEY` — required for perception and animation planning endpoints.
- `REPLICATE_API_TOKEN` — required for segmentation and inpainting endpoints.
