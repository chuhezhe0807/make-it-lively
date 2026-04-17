# Make It Lively — Backend e2e Smoke Test (Live APIs)

Manual checklist for verifying the full backend pipeline
(`upload → perception → segment → inpaint → plan-animation`) against **real**
Anthropic + Replicate services. The automated sibling of this doc is
`test_pipeline.py`, which runs the same flow with the external calls stubbed
out from snapshot fixtures — use that for CI, and use this before cutting a
release or validating a significant pipeline change.

## Prerequisites

- `ANTHROPIC_API_KEY` and `REPLICATE_API_TOKEN` exported in your shell.
- Backend running: `cd backend && uv run uvicorn app.main:app --reload`.

## Reference image

The pytest fixture uses a deterministic 128×128 PNG with an orange block
("cat") and a red block ("ball"). Extract a copy to disk with:

```bash
cd backend
uv run python -c "from tests.e2e.test_pipeline import _reference_image_bytes; \
  open('/tmp/reference.png', 'wb').write(_reference_image_bytes())"
```

## Reference prompt

```
the cat chases the ball and the ball bounces
```

## Steps

1. **Upload**

   ```bash
   curl -sS -F file=@/tmp/reference.png http://localhost:8000/api/upload
   ```

   - Expect: `201`, body `{"image_id": "<uuid>", "width": 128, "height": 128}`.
   - Record `<id>` from the response for the remaining steps.
   - Verify: `backend/storage/images/<id>.png` exists.

2. **Perception**

   ```bash
   curl -sS -X POST -H "Content-Type: application/json" \
     -d '{"image_id": "<id>"}' \
     http://localhost:8000/api/perception
   ```

   - Expect: `200`, `elements` array with plausible labels + bboxes that lie
     inside `[0, 128]`. Repeat calls should return the exact same body (cache
     hit).
   - Verify: `backend/storage/perception/<id>.json` exists.

3. **Segment**

   Copy the `elements` array from step 2 into the next request:

   ```bash
   curl -sS -X POST -H "Content-Type: application/json" \
     -d '{"image_id": "<id>", "elements": [...]}' \
     http://localhost:8000/api/segment
   ```

   - Expect: `200`, a `layers` array whose URLs point to
     `/storage/layers/<id>/<element_id>.png`.
   - Verify: each PNG exists on disk and is a transparent RGBA cut-out.

4. **Inpaint**

   Build `masks` as `[{"bbox": <bbox>}, ...]` from the same elements:

   ```bash
   curl -sS -X POST -H "Content-Type: application/json" \
     -d '{"image_id": "<id>", "masks": [...]}' \
     http://localhost:8000/api/inpaint
   ```

   - Expect: `200`, `{"background_url": "/storage/layers/<id>/background.png"}`.
   - Verify: `background.png` exists at that path and has no obvious seams.

5. **Plan animation**

   ```bash
   curl -sS -X POST -H "Content-Type: application/json" \
     -d '{"image_id": "<id>", "elements": [...], "prompt": "the cat chases the ball and the ball bounces"}' \
     http://localhost:8000/api/plan-animation
   ```

   - Expect: `200`, `plan` array with one entry per input element, each using
     only the 5 supported primitives (`translate`, `rotate`, `scale`,
     `opacity`, `path-follow`) and sensible `duration_ms` / `easing` / `loop`
     fields.

6. **Frontend smoke**

   Start the frontend (`cd frontend && npm run dev`), open
   http://localhost:5173, upload the same reference image, and confirm:

   - The editor's three-step pipeline runs without error and shows element
     thumbnails + the reconstructed canvas.
   - Typing the reference prompt + clicking **Make it Lively** produces a
     playable animation; Play / Pause / Reset all behave.
   - **Export GIF** downloads `make-it-lively.gif` of ≥ 2 seconds.

## Cleanup

```bash
rm -rf backend/storage/images/* backend/storage/layers/* backend/storage/perception/*
```
