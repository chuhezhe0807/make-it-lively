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

## Quality checks

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

## Environment variables

- `ANTHROPIC_API_KEY` — required for perception and animation planning endpoints.
- `REPLICATE_API_TOKEN` — required for segmentation and inpainting endpoints.
